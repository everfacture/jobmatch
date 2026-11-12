#!/bin/sh
# Install a pre-push hook that runs the privacy gate with the local denylist.
set -e
root=$(git rev-parse --show-toplevel)
hook="$root/.git/hooks/pre-push"
cat > "$hook" <<'HOOK'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/scripts/privacy_check.py"
HOOK
chmod +x "$hook"
echo "installed $hook"
