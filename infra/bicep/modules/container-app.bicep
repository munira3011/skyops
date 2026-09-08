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

@description('Managed identity is used for ACR pull instead of admin credentials - no registry password is ever stored in this template.')
param useManagedIdentityForRegistry bool = true

// items(object) turns {KEY: value} into [{key, value}, ...], iterable via for. Computed here as
// their own variables rather than inline inside concat() below - Bicep only allows a for-
// expression as the direct value of a resource/module/variable/output declaration or a
// resource/module property (BCP138), not nested inside an arbitrary function call.
var secretNames = items(secretEnvVars)
var secretEnvRefs = [for s in secretNames: {
  name: s.key
  secretRef: s.key
}]

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'SystemAssigned'
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
        for s in secretNames: {
          name: s.key
          value: s.value
        }
      ]
      registries: !empty(registryServer) && useManagedIdentityForRegistry
        ? [
            {
              server: registryServer
              identity: 'system'
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
output principalId string = containerApp.identity.principalId
