// Generic Azure Container App module, reused for all three SkyOps services (backend, frontend,
// litellm-proxy) so their infra definitions stay identical in shape - only the values passed in
// from main.bicep differ. See docs/architecture.md for what each service is.

@description('Container App name (also becomes its DNS name within the environment).')
param name string

param location string = resourceGroup().location

@description('Resource ID of the shared Container Apps Environment (Microsoft.App/managedEnvironments).')
param environmentId string

@description('Full image reference, e.g. myregistry.azurecr.io/skyops-backend:<tag>.')
param containerImage string

@description('Port the container listens on inside the app.')
param targetPort int

@description('If true, the app gets a public FQDN. If false, it is reachable only from other apps in the same environment - use this for litellm-proxy, which should never be called directly by anything outside the backend.')
param externalIngress bool = false

@description('Non-secret environment variables.')
param envVars array = []

@description('Secret environment variables as a name->value map. Values must come from Key Vault references or deployment parameters, never hardcoded here or in main.bicep. An object, not an array of {name,value} pairs, because @secure() only supports string/object types (BCP124) - found on the first real deployment attempt, see docs/progress.md.')
@secure()
param secretEnvVars object = {}

param cpu string = '0.5'
param memory string = '1Gi'
param minReplicas int = 1
param maxReplicas int = 2

@description('Container registry login server, e.g. myregistry.azurecr.io. Leave empty if containerImage is a public image (e.g. during first bootstrap before any image has been pushed).')
param registryServer string = ''

@description('Resource ID of a user-assigned managed identity to pull from the registry with - no registry password is ever stored in this template. A shared identity (created and granted AcrPull in main.bicep before any container app exists) rather than this app\'s own system-assigned identity, to avoid a circular dependency: a system-assigned identity only exists once the app itself has already been created, but the app needs pull access before it can start at all.')
param userAssignedIdentityId string = ''

// items(object) turns {KEY: value} into [{key, value}, ...], iterable via for. Computed here as
// their own variables rather than inline inside concat() below - Bicep only allows a for-
// expression as the direct value of a resource/module/variable/output declaration or a
// resource/module property (BCP138), not nested inside an arbitrary function call.
//
// Container Apps' internal *secret name* (its own secret-store key) and the *environment
// variable name* exposed to the container are two different namespaces with different rules -
// found on the first real deployment attempt (ContainerAppInvalidSecretName), see
// docs/progress.md. The env var name must stay exactly as given (e.g. ANTHROPIC_API_KEY - the
// app reads it via os.environ, uppercase-with-underscores by convention); the secret name must
// be lowercase alphanumeric/hyphens only, so it's derived separately and only used as the
// secretRef, never shown to the app itself.
var secretNames = items(secretEnvVars)
var secretEntries = [for s in secretNames: {
  envName: s.key
  secretName: replace(toLower(s.key), '_', '-')
  value: s.value
}]
var secretEnvRefs = [for e in secretEntries: {
  name: e.envName
  secretRef: e.secretName
}]

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: !empty(userAssignedIdentityId)
    ? {
        type: 'UserAssigned'
        userAssignedIdentities: {
          '${userAssignedIdentityId}': {}
        }
      }
    : {
        type: 'None'
      }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      ingress: {
        external: externalIngress
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        for e in secretEntries: {
          name: e.secretName
          value: e.value
        }
      ]
      registries: !empty(registryServer) && !empty(userAssignedIdentityId)
        ? [
            {
              server: registryServer
              identity: userAssignedIdentityId
            }
          ]
        : []
    }
    template: {
      containers: [
        {
          name: name
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(envVars, secretEnvRefs)
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
output name string = containerApp.name
