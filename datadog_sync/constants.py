# Environment variables
DD_SOURCE_API_URL = "DD_SOURCE_API_URL"
DD_SOURCE_API_KEY = "DD_SOURCE_API_KEY"
DD_SOURCE_APP_KEY = "DD_SOURCE_APP_KEY"
DD_DESTINATION_API_URL = "DD_DESTINATION_API_URL"
DD_DESTINATION_API_KEY = "DD_DESTINATION_API_KEY"
DD_DESTINATION_APP_KEY = "DD_DESTINATION_APP_KEY"

# Default variables
DEFAULT_API_URL = "https://api.datadoghq.com/"
DEFAULT_STATE_PATH = "state/"
DEFAULT_STATE_NAME = "terraform.tfstate.{}"

TERRAFORMER_FILTER = "--filter={}"
RESOURCE_FILE_PATH = "resources/{0}/{0}.tf.json"
RESOURCE_DIR = "resources/{}"
RESOURCE_STATE_PATH = "resources/{}/terraform.tfstate"
RESOURCE_OUTPUT_PATH = "resources/{}/outputs.tf.json"
RESOURCE_VARIABLES_PATH = "resources/{}/variables.tf.json"
RESOURCE_OUTPUT_CONNECT = "${{data.terraform_remote_state.{}.outputs.{}}}"
