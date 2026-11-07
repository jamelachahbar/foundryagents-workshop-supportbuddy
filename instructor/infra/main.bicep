// SupportBuddy lab: every shared Azure resource the workshop needs, in one resource group.
// Deploy with azd from the instructor folder: `azd up` (see instructor/setup.md).
targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name; also names the resource group (rg-<name>).')
param environmentName string

@description('Region for Foundry, the MCP server and the logs.')
param location string

@description('Region for Azure AI Search (Foundry IQ knowledge base). Empty = same as location.')
param searchLocation string = ''

@description('Object ID of the person running the setup (azd sets it). Gets the roles to build the shared resources.')
param principalId string = ''

@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

@description('Optional: object ID of a Microsoft Entra group with all participants. Gets the participant roles.')
param participantsGroupId string = ''

@description('Chat model for the agents and the evaluators.')
param chatModelName string = 'model-router'
param chatModelVersion string = '2025-11-18'
@description('Deployment name handed to participants. Match an existing deployment when reusing an environment.')
param chatModelDeploymentName string = 'model-router'
@allowed(['GlobalStandard', 'DataZoneStandard'])
param chatModelSku string = 'GlobalStandard'
@description('Model-router routing profile. No model subset is specified, so all available models in the router version are eligible.')
@allowed(['balanced', 'quality', 'cost'])
param chatModelRoutingMode string = 'balanced'
@description('Capacity in thousands of tokens per minute, shared by all participants.')
param chatModelCapacity string = '100'

@description('Embedding model for Foundry Memory.')
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'
param embeddingModelSku string = 'GlobalStandard'
param embeddingModelCapacity int = 50

@description('Provision the fixed evaluation judge by default. Disable only for sessions without Foundry scoring; requires model availability and quota.')
param deployEvaluationModel bool = true
param evaluationModelName string = 'gpt-5-mini'
param evaluationModelVersion string = '2025-08-07'
@description('Use a name distinct from the chat and embedding deployments.')
param evaluationModelDeploymentName string = 'supportbuddy-judge'
@allowed(['GlobalStandard', 'DataZoneStandard', 'Standard'])
param evaluationModelSku string = 'GlobalStandard'
@description('Judge capacity in thousands of tokens per minute, shared by participants.')
param evaluationModelCapacity string = '10'

@description('Extra tags as key=value pairs separated by semicolons, for example "SecurityControl=Ignore;owner=me", when policies require them.')
param resourceTags string = ''

@description('Optional: URL (ending in /mcp) of a network MCP server that already runs elsewhere, or "none". Either skips Container Apps.')
param externalNetworkMcpUrl string = ''

@description('Set by azd once the MCP server app exists, so provisioning again keeps the deployed image.')
param networkMcpExists bool = false

var extraTags = toObject(filter(split(resourceTags, ';'), pair => contains(pair, '=')), pair => trim(split(pair, '=')[0]), pair => trim(substring(pair, indexOf(pair, '=') + 1)))
var tags = union(extraTags, { 'azd-env-name': environmentName, purpose: 'SupportBuddy workshop (synthetic data)' })

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module lab 'resources.bicep' = {
  name: 'supportbuddy-lab'
  scope: rg
  params: {
    location: location
    searchLocation: empty(searchLocation) ? location : searchLocation
    tags: tags
    principalId: principalId
    principalType: principalType
    participantsGroupId: participantsGroupId
    chatModel: {
      name: chatModelName
      version: chatModelVersion
      deploymentName: chatModelDeploymentName
      sku: chatModelSku
      capacity: int(chatModelCapacity)
      routingMode: chatModelRoutingMode
    }
    embeddingModel: { name: embeddingModelName, version: embeddingModelVersion, sku: embeddingModelSku, capacity: embeddingModelCapacity }
    deployEvaluationModel: deployEvaluationModel
    evaluationModel: {
      name: evaluationModelName
      version: evaluationModelVersion
      deploymentName: evaluationModelDeploymentName
      sku: evaluationModelSku
      capacity: int(evaluationModelCapacity)
    }
    externalNetworkMcpUrl: externalNetworkMcpUrl
    networkMcpExists: networkMcpExists
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_AI_PROJECT_ENDPOINT string = lab.outputs.projectEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = lab.outputs.chatDeployment
output AZURE_AI_EMBEDDING_DEPLOYMENT_NAME string = lab.outputs.embeddingDeployment
output AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME string = lab.outputs.evaluationDeployment
output AZURE_AI_SEARCH_ENDPOINT string = lab.outputs.searchEndpoint
output FOUNDRY_ACCOUNT_ID string = lab.outputs.accountId
output FOUNDRY_PROJECT_ID string = lab.outputs.projectId
output SUPPORTBUDDY_GUARDRAIL_ID string = lab.outputs.guardrailId
output NETWORK_MCP_URL string = lab.outputs.networkMcpUrl
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = lab.outputs.registryEndpoint
output APPLICATIONINSIGHTS_RESOURCE_ID string = lab.outputs.applicationInsightsResourceId
