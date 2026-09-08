// SkyOps infra: one Container Apps Environment hosting the three independent services
// (litellm-proxy, backend, frontend) as separate Container Apps, matching CLAUDE.md's
// "three separate Docker images/Azure Container Apps" design and docs/architecture.md's service
// diagram. Deployed by .github/workflows/deploy.yml after each image is built and pushed.
//
// All secret values (provider keys, app API keys) are deployment parameters, never literals in
// this file - the workflow supplies them from GitHub Actions secrets. Nothing here should ever
// contain a real key.

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short, unique-ish prefix for resource names, e.g. "skyops-dev" or "skyops-prod".')
param namePrefix string = 'skyops'

@description('Container registry login server (e.g. myregistry.azurecr.io) that the three images below were pushed to.')
param registryServer string

@description('Resource ID of that same container registry (Microsoft.ContainerRegistry/registries) - used to grant each app\'s managed identity AcrPull, so no registry password is ever stored as a secret.')
param registryResourceId string

@description('Full image references for each service, e.g. myregistry.azurecr.io/skyops-backend:<git-sha>. Supplied per-deploy by CI so each run pins an exact, traceable build.')
param backendImage string
param frontendImage string
param litellmProxyImage string

@secure()
@description('Upstream OpenAI key for litellm-proxy config.yaml\'s "primary" model.')
param openaiApiKey string

@secure()
@description('Upstream Anthropic key for litellm-proxy config.yaml\'s "fallback" model.')
param anthropicApiKey string

@secure()
@description('Shared secret clients (the backend) use to authenticate to litellm-proxy. Maps to LITELLM_MASTER_KEY on the proxy side and LITELLM_API_KEY on the backend side - same value, two names, per each service\'s own .env.example.')
param litellmSharedKey string

@secure()
@description('Customer-facing API key - required on every backend route except /health. Shared by the frontend to authenticate its calls to the backend.')
param skyopsApiKey string

@secure()
@description('Additional key required on /ops/* routes on top of skyopsApiKey, so a leaked customer key alone can\'t reach staff/approval endpoints.')
param skyopsOpsApiKey string

var logAnalyticsName = '${namePrefix}-logs'
var environmentName = '${namePrefix}-env'
// Computed once and reused for both each module's `name` param and the role-assignment `name`
// below - a role assignment's `name` must be resolvable before deployment starts, so it can't
// reference a module output like `frontend.outputs.name` (BCP120: found on the first real
// deployment attempt, see docs/progress.md) even though that output just echoes this same,
// already-known string back.
var litellmProxyName = '${namePrefix}-litellm-proxy'
var backendName = '${namePrefix}-backend'
var frontendName = '${namePrefix}-frontend'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// litellm-proxy: internal-only ingress. The only service holding real provider keys; nothing
// outside this environment (and no service other than backend) should ever call it directly.
module litellmProxy 'modules/container-app.bicep' = {
  name: 'litellm-proxy-deploy'
  params: {
    name: litellmProxyName
    location: location
    environmentId: containerAppsEnvironment.id
    containerImage: litellmProxyImage
    targetPort: 4000
    externalIngress: false
    registryServer: registryServer
    secretEnvVars: [
      { name: 'OPENAI_API_KEY', value: openaiApiKey }
      { name: 'ANTHROPIC_API_KEY', value: anthropicApiKey }
      { name: 'LITELLM_MASTER_KEY', value: litellmSharedKey }
    ]
  }
}

// backend: external ingress (the frontend and any direct API/ops testing need to reach it).
// LITELLM_BASE_URL points at litellm-proxy's environment-internal FQDN - never localhost, that
// only works for the docker-compose/local-dev path (see docker-compose.yml's own comment on
// this same distinction).
module backend 'modules/container-app.bicep' = {
  name: 'backend-deploy'
  params: {
    name: backendName
    location: location
    environmentId: containerAppsEnvironment.id
    containerImage: backendImage
    targetPort: 8000
    externalIngress: true
    registryServer: registryServer
    envVars: [
      { name: 'LITELLM_BASE_URL', value: 'https://${litellmProxy.outputs.fqdn}' }
      { name: 'LITELLM_MODEL', value: 'primary' }
    ]
    secretEnvVars: [
      { name: 'LITELLM_API_KEY', value: litellmSharedKey }
      { name: 'SKYOPS_API_KEY', value: skyopsApiKey }
      { name: 'SKYOPS_OPS_API_KEY', value: skyopsOpsApiKey }
    ]
  }
}

// frontend: external ingress, the only customer-facing surface. Never given the ops key - it
// authenticates as a normal customer client, same as local dev (see frontend/.env.example).
module frontend 'modules/container-app.bicep' = {
  name: 'frontend-deploy'
  params: {
    name: frontendName
    location: location
    environmentId: containerAppsEnvironment.id
    containerImage: frontendImage
    targetPort: 8501
    externalIngress: true
    registryServer: registryServer
    envVars: [
      { name: 'SKYOPS_API_BASE_URL', value: 'https://${backend.outputs.fqdn}' }
    ]
    secretEnvVars: [
      { name: 'SKYOPS_API_KEY', value: skyopsApiKey }
    ]
  }
}

// Grant each app's system-assigned identity permission to pull from the registry - the
// container-app module configures `registries: [{ identity: 'system' }]`, which requires this
// role assignment to actually work. No registry password/secret involved.
// Assumes the registry lives in this same resource group (true for this project's scope); if it
// doesn't, deploy these three role assignments as a separate module scoped to the registry's own
// resource group instead.
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(registryResourceId, '/'))
}

resource litellmProxyAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryResourceId, litellmProxyName, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: litellmProxy.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryResourceId, backendName, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: backend.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

resource frontendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryResourceId, frontendName, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: frontend.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

output backendFqdn string = backend.outputs.fqdn
output frontendFqdn string = frontend.outputs.fqdn
output litellmProxyFqdn string = litellmProxy.outputs.fqdn
