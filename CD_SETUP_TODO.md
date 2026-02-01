# CD Setup — Manual Steps

Everything below requires action in the GitHub UI. The infrastructure
(S3 bucket, DynamoDB lock table, OIDC provider, IAM role) is already deployed
and the Terraform state has been migrated to S3.

## 1. Add GitHub Secrets

Go to: https://github.com/badjacent/feldenkrais/settings/secrets/actions

Add each of these repository secrets:

| Secret | Value |
|--------|-------|
| `AWS_ROLE_ARN` | `arn:aws:iam::355854622119:role/feldenkrais-github-deploy` |
| `GROQ_API_KEY` | *(from .env)* |
| `DATABASE_URL` | *(from .env)* |
| `GOOGLE_API_KEY` | *(from .env)* |
| `ANTHROPIC_API_KEY` | *(from .env)* |
| `OPENAI_API_KEY` | *(from .env)* |

## 2. Commit and Push

The following changes are staged and ready to commit:

- `terraform/ci/` — bootstrap config (already applied)
- `terraform/ci/.terraform.lock.hcl` — CI provider lock
- `.github/workflows/deploy.yml` — GitHub Actions workflow
- `terraform/main.tf` — S3 backend block added
- `terraform/.terraform.lock.hcl` — provider lock (now tracked)
- `.gitignore` — un-ignored lock file
- `terraform/OPERATIONS.md` — CD documentation added

Once secrets are configured and changes pushed to `master`, the first
automated deploy will run.

## 3. Verify

After push:
1. Check GitHub Actions tab: https://github.com/badjacent/feldenkrais/actions
2. The "Deploy" workflow should trigger and succeed
3. Verify the web app still loads at https://aionyourside.net

## Troubleshooting

- **"Could not assume role"**: The OIDC trust policy is scoped to
  `repo:badjacent/feldenkrais:ref:refs/heads/master`. Make sure
  `AWS_ROLE_ARN` secret matches the ARN above exactly.

- **"Error acquiring state lock"**: Someone else is running `terraform apply`.
  Wait or check DynamoDB table `feldenkrais-terraform-lock`.

- **Missing API key**: If a `TF_VAR_*` secret is empty, Terraform will use
  the default (empty string). The `groq_api_key` conditionally enables
  transcription/youtube Lambdas — set it to keep them.

## Delete This File

This file can be deleted after setup is complete.
