# CI/CD Quick Reference

## Branch Workflow

1. **Create feature branch** from `main`:
   ```bash
   git checkout main
   git pull
   git checkout -b feat/my-feature
   ```

2. **Make commits** following conventional commits:
   ```bash
   git commit -m "feat: add new feature"
   git commit -m "fix: resolve bug in feature"
   git commit -m "docs: update README"
   ```

3. **Push and create PR**:
   ```bash
   git push -u origin feat/my-feature
   gh pr create --title "feat: add new feature" --body "Description..."
   ```

4. **Automated checks run**:
   - ✓ Linting (ruff)
   - ✓ Type checking (mypy)
   - ✓ Python tests
   - ✓ Docker build
   - ✓ Commit message format

5. **After approval and merge**:
   - CI runs on main branch
   - Docker image published to GHCR with `latest` tag

## Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Formatting, missing semicolons, etc
- `refactor`: Code restructuring without changing behavior
- `perf`: Performance improvement
- `test`: Adding/updating tests
- `build`: Build system changes
- `ci`: CI/CD changes
- `chore`: Maintenance tasks
- `revert`: Revert previous commit

### Examples
```bash
feat: add rate limiting for virtual keys
fix(database): resolve connection pool leak
docs(README): update installation instructions
refactor(server): extract auth logic to middleware
test(providers): add unit tests for cost calculation
ci: add security scanning workflow
```

## Release Process

1. **Prepare release**:
   ```bash
   git checkout main
   git pull
   ```

2. **Create and push tag**:
   ```bash
   git tag -a v1.2.0 -m "Release v1.2.0"
   git push origin v1.2.0
   ```

3. **Automated release**:
   - GitHub Release created with changelog
   - Docker image built and pushed with version tag
   - `latest` tag updated

## Manual Workflows

Trigger via GitHub UI or `gh` CLI:

```bash
# Run CI manually
gh workflow run ci.yml

# Create release
gh workflow run release.yml -f version=v1.2.0

# Run security audit
gh workflow run security-audit.yml
```

## Docker Images

Images are published to GitHub Container Registry (GHCR):

```bash
# Pull latest
docker pull ghcr.io/rohitpaul/llm-gateway:latest

# Pull specific version
docker pull ghcr.io/rohitpaul/llm-gateway:v1.2.0

# Pull by commit sha
docker pull ghcr.io/rohitpaul/llm-gateway:abc1234
```

## Troubleshooting

### CI Fails

1. Check the workflow logs in GitHub Actions
2. Run checks locally:
   ```bash
   ruff check app/
   ruff format --check app/
   mypy app/ --ignore-missing-imports
   pytest tests/ -v
   ```

### Commit Lint Fails

If your PR commits fail linting:

1. Check commit format: `type: subject`
2. Interactive rebase to fix commits:
   ```bash
   git rebase -i HEAD~N  # N = number of commits to edit
   # Change 'pick' to 'reword' for each commit
   # Fix commit messages
   git push -f
   ```

### Docker Build Fails

1. Test locally:
   ```bash
   docker build -t llm-gateway:test .
   docker run --rm -e GATEWAY_ADMIN_KEY=sk-test llm-gateway:test
   ```

## Branch Protection

The `main` branch is protected:
- ✓ Requires PR with 1 approval
- ✓ Requires passing CI checks
- ✓ Requires linear history (rebase, not merge)
- ✓ Requires branches to be up-to-date
- ✓ Dismisses stale reviews on new commits

## Status Badges

Add to README.md:

```markdown
[![CI](https://github.com/rohitpaul/llm-gateway/workflows/CI/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/ci.yml)
[![Security](https://github.com/rohitpaul/llm-gateway/workflows/Security%20Audit/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/security-audit.yml)
[![Release](https://github.com/rohitpaul/llm-gateway/workflows/Release/badge.svg)](https://github.com/rohitpaul/llm-gateway/actions/workflows/release.yml)
```
