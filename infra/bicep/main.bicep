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

@description('Resource ID of that same container registry (Microsoft.ContainerRegistry/registries) - used to grant the shared pull identity AcrPull, so no registry password is ever stored as a secret.')
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

var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

// Assumes the registry lives in this same resource group (true for this project's scope); if it
// doesn't, deploy the identity/role assignment below as a separate module scoped to the
// registry's own resource group instead.
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(registryResourceId, '/'))
}

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

// One shared user-assigned identity, granted AcrPull *before* any container app exists - a
// system-assigned identity's principalId only exists once its container app has already been
// created, but the app needs AcrPull to pull its image before it can start at all. That's a
// deadlock on a from-scratch registry/environment (found on the first real deployment attempt:
// litellm-proxy never got a revision - "Operation expired" - and its AcrPull role assignment,
// which depended on its own not-yet-existing output, was never even attempted; the registry
// showed zero AcrPull grants. See docs/progress.md). A user-assigned identity breaks the cycle:
// it and its role assignment are both created upfront, independent of any container app.
resource acrPullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-acr-pull-identity'
  location: location
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryResourceId, acrPullIdentity.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: acrPullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
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
    userAssignedIdentityId: acrPullIdentity.id
    healthCheckPath: '/health/readiness'
    secretEnvVars: {
      OPENAI_API_KEY: openaiApiKey
      ANTHROPIC_API_KEY: anthropicApiKey
      LITELLM_MASTER_KEY: litellmSharedKey
    }
  }
  dependsOn: [
    acrPullRoleAssignment
  ]
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
    userAssignedIdentityId: acrPullIdentity.id
    healthCheckPath: '/health'
    // Pinned to a single replica: runtime.py's api_graph uses an in-memory LangGraph
    // checkpointer (InMemorySaver) - conversation state lives in one process's RAM, never
    // shared across replicas. With the module's default maxReplicas (2), a follow-up message
    // could land on a replica that never saw the thread's earlier turns, silently losing
    // context - found on the first live deployment (see docs/progress.md). litellm-proxy and
    // the frontend don't have this problem (no cross-request state of their own), so only
    // backend needs this override.
    maxReplicas: 1
    envVars: [
      { name: 'LITELLM_BASE_URL', value: 'https://${litellmProxy.outputs.fqdn}' }
      { name: 'LITELLM_MODEL', value: 'primary' }
    ]
    secretEnvVars: {
      LITELLM_API_KEY: litellmSharedKey
      SKYOPS_API_KEY: skyopsApiKey
      SKYOPS_OPS_API_KEY: skyopsOpsApiKey
    }
  }
  dependsOn: [
    acrPullRoleAssignment
  ]
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
    userAssignedIdentityId: acrPullIdentity.id
    healthCheckPath: '/_stcore/health'
    envVars: [
      { name: 'SKYOPS_API_BASE_URL', value: 'https://${backend.outputs.fqdn}' }
    ]
    secretEnvVars: {
      SKYOPS_API_KEY: skyopsApiKey
    }
  }
  dependsOn: [
    acrPullRoleAssignment
  ]
}

output backendFqdn string = backend.outputs.fqdn
output frontendFqdn string = frontend.outputs.fqdn
output litellmProxyFqdn string = litellmProxy.outputs.fqdn
