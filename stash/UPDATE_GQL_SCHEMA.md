# Updating Stash GraphQL Schema

The GraphQL schema files in `schema/` are reference documentation from the upstream Stash project.

## Update Process

```bash
# If stash-upstream remote doesn't exist yet, add it:
git remote add stash-upstream https://github.com/stashapp/stash.git

# Fetch latest upstream
git fetch stash-upstream develop

# Remove old schema (if it exists)
git rm -r stash/schema/ 2>/dev/null || true

# Add updated schema
git read-tree --prefix=stash/schema/ -u stash-upstream/develop:graphql/schema

# Commit the update
git commit -m "chore: update Stash GraphQL schema from upstream"
```

## Note

The actual GraphQL queries used by this project are in `fragments.py`, not these `.graphql` files. The schema files serve as API reference documentation only.
