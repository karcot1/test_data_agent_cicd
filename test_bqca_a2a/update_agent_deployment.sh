# 1. Grab your authenticated token
ACCESS_TOKEN=$(gcloud auth print-access-token)
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
LOCATION="us-central1"
ENGINE_ID="911096566461235200"
TAG=${TAG}

# 2. Supply your new container reference (version tag or registry digest)
NEW_IMAGE_URI="us-docker.pkg.dev/${PROJECT_ID}/agent-repo/test_bqca_a2a:$TAG"

# 3. Run the PATCH call
RESPONSE=$(curl -s -X PATCH \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "spec": {
      "agentFramework": "google-adk",
      "container_spec": {
        "imageUri": "'"${NEW_IMAGE_URI}"'"
      }
    }
  }' \
  "https://${LOCATION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${LOCATION}/reasoningEngines/${ENGINE_ID}?updateMask=spec.agent_framework,spec.container_spec")

echo "Operation triggered:"
if command -v jq >/dev/null 2>&1; then
  echo "${RESPONSE}" | jq .
elif command -v python3 >/dev/null 2>&1; then
  echo "${RESPONSE}" | python3 -m json.tool 2>/dev/null || echo "${RESPONSE}"
else
  echo "${RESPONSE}"
fi