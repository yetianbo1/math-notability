"""Public product name and legacy identities retained for compatibility.

Settings, shell ProgIDs and the NTB image namespace remain under Viewer.
The installer migrates owned Viewer program files to the Editor identity.
"""

PRODUCT_NAME = 'NotabilityEditor'
ORGANIZATION_NAME = PRODUCT_NAME
SETTINGS_APPLICATION = PRODUCT_NAME
SETTINGS_DISPLAY_APPLICATION = 'Display'
LEGACY_DISPLAY_NAMES = ('Notability Viewer', 'NotabilityViewer')
LEGACY_APP_ID = 'NotabilityViewer.Desktop'
LEGACY_CAPABILITIES = r'Software\NotabilityViewer\Capabilities'
LEGACY_UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotabilityViewer'
