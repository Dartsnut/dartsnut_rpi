#!/usr/bin/env bash
set -euo pipefail

# Local bootstrap
supabase start
supabase db reset

echo "Local Supabase stack is ready."
echo "To apply schema to hosted project:"
echo "  supabase link --project-ref <project_ref>"
echo "  supabase db push"
