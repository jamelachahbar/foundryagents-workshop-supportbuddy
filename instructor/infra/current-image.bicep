// Reads the image an app already runs, so provisioning again does not replace what `azd deploy` put there.
param exists bool
param name string

resource app 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: name
}

output image string = exists ? app!.properties.template.containers[0].image : ''
