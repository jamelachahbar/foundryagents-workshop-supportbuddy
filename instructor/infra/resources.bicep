// The shared lab resources. Names are derived from the resource group so several workshops can coexist.
param location string
param searchLocation string
param tags object
param principalId string
param principalType string
param participantsGroupId string
param chatModel object
param embeddingModel object
param deployEvaluationModel bool
param evaluationModel object
param externalNetworkMcpUrl string
param networkMcpExists bool

var suffix = uniqueString(resourceGroup().id)
var projectName = 'supportbuddy'
var hostNetworkMcp = empty(externalNetworkMcpUrl)

// Built-in roles (IDs verified with `az role definition list --name ...`).
var roles = {
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  foundryProjectManager: 'eadc314b-1a2d-4efa-be10-5d325db5065e'
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  cognitiveServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
  monitoringReader: '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
  logAnalyticsReader: '73c42c96-874c-492b-b04d-ab87d138a893'
}

// ----- Foundry: account, project, models, guardrail -----------------------------------------

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'sb-foundry-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'sb-foundry-${suffix}'
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'SupportBuddy workshop'
    description: 'Synthetic connectivity-support lab: participants build and run their agents here.'
  }
}

// The account accepts one change at a time, so its children are created one after the other.
resource guardrail 'Microsoft.CognitiveServices/accounts/raiPolicies@2025-06-01' = {
  parent: account
  name: 'supportbuddy-guardrail'
  properties: loadJsonContent('../guardrail.json').properties
  dependsOn: [project]
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  parent: account
  name: chatModel.deploymentName
  sku: { name: chatModel.sku, capacity: chatModel.capacity }
  properties: union({
    model: { format: 'OpenAI', name: chatModel.name, version: chatModel.version }
  }, chatModel.name == 'model-router' ? {
    routing: {
      mode: chatModel.routingMode
      // Optional allow-list: verify these observed model/version pairs in Foundry first.
      // Uncomment to restrict the router; leaving this commented keeps the full pool eligible.
      // models: [
      //   { format: 'OpenAI', name: 'gpt-5.6-terra', version: '2026-07-09' },
      //   { format: 'OpenAI', name: 'gpt-6-sol', version: '2026-09-22' },
      //   { format: 'OpenAI', name: 'gpt-5.4-mini', version: '2026-03-17' }
      // ]
    }
  } : {})
  dependsOn: [guardrail]
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'supportbuddy-embedding'
  sku: { name: embeddingModel.sku, capacity: embeddingModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: embeddingModel.name, version: embeddingModel.version }
  }
  dependsOn: [chatDeployment]
}

resource evaluationDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployEvaluationModel) {
  parent: account
  name: evaluationModel.deploymentName
  sku: { name: evaluationModel.sku, capacity: evaluationModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: evaluationModel.name, version: evaluationModel.version }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [embeddingDeployment]
}

// ----- Azure AI Search: the Foundry IQ knowledge base (runbooks) -----------------------------

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'sb-search-${suffix}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    semanticSearch: 'free'
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
  }
}

// ----- Shared tracing (also needed when the network MCP server is external or local) ---------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'sb-logs-${suffix}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'sb-insights-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    RetentionInDays: 30
    IngestionMode: 'LogAnalytics'
  }
}

// ----- Network MCP server on Azure Container Apps (skipped when an external URL is given) -----

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (hostNetworkMcp) {
  name: 'sbacr${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

// Created before the app, so it can already pull from the registry when the app starts.
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (hostNetworkMcp) {
  name: 'sb-mcp-pull-${suffix}'
  location: location
  tags: tags
}

resource mcpPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (hostNetworkMcp) {
  scope: registry
  name: guid(registry.id, pullIdentity.id, roles.acrPull)
  properties: {
    principalId: pullIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = if (hostNetworkMcp) {
  name: 'sb-env-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

module deployedImage 'current-image.bicep' = if (hostNetworkMcp) {
  name: 'network-mcp-current-image'
  params: { exists: networkMcpExists, name: 'supportbuddy-network-mcp' }
}

resource networkMcp 'Microsoft.App/containerApps@2024-03-01' = if (hostNetworkMcp) {
  name: 'supportbuddy-network-mcp'
  location: location
  // azd deploys the MCP server image (src/mcp_server/Dockerfile) into the app with this tag.
  tags: union(tags, { 'azd-service-name': 'network-mcp' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${pullIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [{ server: registry!.properties.loginServer, identity: pullIdentity.id }]
    }
    template: {
      containers: [
        {
          name: 'main'
          // Placeholder until `azd deploy` builds and pushes the MCP server image; kept on later provisions.
          image: networkMcpExists ? deployedImage!.outputs.image : 'mcr.microsoft.com/k8se/quickstart:latest'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
  dependsOn: [mcpPull]
}

// ----- Role assignments ----------------------------------------------------------------------

// Foundry Memory (Stage 11) uses the project identity; hosted agents have their own runtime identity.
var projectModelRoles = [roles.cognitiveServicesOpenAIUser, roles.foundryUser]

resource projectUsesModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in projectModelRoles: {
  scope: account
  name: guid(account.id, project.id, role)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

// Foundry Memory also calls the embedding deployment with the Foundry resource's own identity.
resource accountUsesModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, 'account-identity', roles.cognitiveServicesOpenAIUser)
  properties: {
    principalId: account.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAIUser)
  }
}

// Optional knowledge-base planning and answer synthesis call the model as Search, not as the project.
resource searchUsesModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, search.id, roles.cognitiveServicesUser)
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

// The toolbox calls the knowledge base with the project's managed identity (Stage 9).
resource projectReadsSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, project.id, roles.searchIndexDataReader)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
  }
}

// The person running the setup builds the knowledge base and the memory store, and can do every stage.
var setupSearchRoles = empty(principalId) ? [] : [roles.searchServiceContributor, roles.searchIndexDataContributor]
var setupFoundryRoles = empty(principalId) ? [] : [roles.foundryUser, roles.foundryProjectManager]

resource setupOnSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in setupSearchRoles: {
  scope: search
  name: guid(search.id, principalId, role)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

resource setupOnFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in setupFoundryRoles: {
  scope: account
  name: guid(account.id, principalId, role)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

// Participants: Foundry User (Stages 1-13), Foundry Project Manager (Stage 14), Search Index Data Reader (Stage 8).
var participantFoundryRoles = empty(participantsGroupId) ? [] : [roles.foundryUser, roles.foundryProjectManager]

resource participantsOnFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in participantFoundryRoles: {
  scope: account
  name: guid(account.id, participantsGroupId, role)
  properties: {
    principalId: participantsGroupId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

resource participantsOnSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(participantsGroupId)) {
  scope: search
  name: guid(search.id, participantsGroupId, roles.searchIndexDataReader)
  properties: {
    principalId: participantsGroupId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
  }
}

var setupMonitoringRoles = empty(principalId) ? [] : [roles.monitoringMetricsPublisher, roles.monitoringReader]
var participantMonitoringRoles = empty(participantsGroupId) ? [] : [roles.monitoringMetricsPublisher, roles.monitoringReader]

resource setupOnInsights 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in setupMonitoringRoles: {
  scope: insights
  name: guid(insights.id, principalId, role)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

resource participantsOnInsights 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in participantMonitoringRoles: {
  scope: insights
  name: guid(insights.id, participantsGroupId, role)
  properties: {
    principalId: participantsGroupId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]

resource setupReadsLogs 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  scope: logs
  name: guid(logs.id, principalId, roles.logAnalyticsReader)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.logAnalyticsReader)
  }
}

resource participantsReadLogs 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(participantsGroupId)) {
  scope: logs
  name: guid(logs.id, participantsGroupId, roles.logAnalyticsReader)
  properties: {
    principalId: participantsGroupId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.logAnalyticsReader)
  }
}

output projectEndpoint string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output chatDeployment string = chatDeployment.name
output embeddingDeployment string = embeddingDeployment.name
output evaluationDeployment string = deployEvaluationModel ? evaluationModel.deploymentName : ''
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output accountId string = account.id
output projectId string = project.id
output guardrailId string = guardrail.id
output networkMcpUrl string = hostNetworkMcp ? 'https://${networkMcp!.properties.configuration.ingress.fqdn}/mcp' : (externalNetworkMcpUrl == 'none' ? '' : externalNetworkMcpUrl)
output registryEndpoint string = hostNetworkMcp ? registry!.properties.loginServer : ''
output applicationInsightsResourceId string = insights.id
