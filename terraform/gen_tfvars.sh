#!/usr/bin/env zsh
# Generate terraform.tfvars from the project .env file.
# Usage: cd terraform && ./gen_tfvars.sh
#
# Reads ../.env, maps env vars to terraform variables, writes terraform.tfvars.
# Reports any vars that are missing from .env.

set -eo pipefail

ENV_FILE="${1:-../.env}"
OUT_FILE="terraform.tfvars"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

# Load .env (skip comments and blank lines)
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    # Strip surrounding quotes
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    eval "export $key=\"\$value\""
done < "$ENV_FILE"

# deploy_sha from git
DEPLOY_SHA="$(git -C .. rev-parse --short HEAD 2>/dev/null || echo 'local')"

missing=()

# emit_var tf_name env_name
emit_var() {
    local tf_name="$1" env_name="$2"
    local val="$(eval echo "\${$env_name:-}")"
    if [[ -z "$val" ]]; then
        missing+=("$tf_name (needs $env_name in .env)")
    else
        printf '%-30s = "%s"\n' "$tf_name" "$val"
    fi
}

{
    echo "# Auto-generated from $ENV_FILE on $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "# Do not edit — re-run ./gen_tfvars.sh to regenerate."
    echo ""

    # environment is always "prod" for deployed infra (KB_ENV=dev is for local dev)
    printf '%-30s = "%s"\n' environment "prod"
    emit_var database_url             DATABASE_URL
    emit_var groq_api_key             GROQ_API_KEY
    emit_var google_api_key           GOOGLE_API_KEY
    emit_var anthropic_api_key        ANTHROPIC_API_KEY
    emit_var openai_api_key           OPENAI_API_KEY
    emit_var google_oauth_client_id   GOOGLE_OAUTH_CLIENT_ID
    emit_var google_oauth_client_secret GOOGLE_OAUTH_CLIENT_SECRET
    emit_var github_client_id         GH_OAUTH_CLIENT_ID
    emit_var github_client_secret     GH_OAUTH_CLIENT_SECRET
    emit_var cookie_secret            COOKIE_SECRET

    echo ""
    printf '%-30s = "%s"\n' deploy_sha "$DEPLOY_SHA"
    printf '%-30s = "%s"\n' search_domain_name "search.aionyourside.net"
    printf '%-30s = "%s"\n' finder_domain_name "finder.becomecomfortable.com"
    printf '%-30s = "%s"\n' interiority_domain_name "interiority.aionyourside.net"
} > "$OUT_FILE"

echo "Wrote $OUT_FILE"

if [[ ${#missing[@]} -gt 0 ]]; then
    echo ""
    echo "Missing variables (not found in $ENV_FILE):"
    for m in "${missing[@]}"; do
        echo "  - $m"
    done
    exit 1
else
    echo "All variables populated."
fi
