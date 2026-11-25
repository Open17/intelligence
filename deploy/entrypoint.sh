#!/bin/sh
# =============================================================================
#  Container entrypoint: load the Anthropic credential from the Docker secret
#  into the environment of the service process only, then run the service.
#
#  sk-ant-oat... is a Claude subscription token (from `claude setup-token`);
#  anything else is an API key from console.anthropic.com.
# =============================================================================
set -eu

secret=/run/secrets/anthropic_credential
if [ -r "$secret" ]; then
    cred=$(tr -d ' \r\n' < "$secret")
    case "$cred" in
        sk-ant-oat*) CLAUDE_CODE_OAUTH_TOKEN=$cred; export CLAUDE_CODE_OAUTH_TOKEN ;;
        ?*)          ANTHROPIC_API_KEY=$cred;       export ANTHROPIC_API_KEY ;;
    esac
    unset cred
else
    echo "entrypoint: $secret is missing or unreadable; Claude calls will fail" >&2
fi

exec "$@"
