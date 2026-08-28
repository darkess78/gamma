# Git Branch Policy

Keep the remote branch list small and purpose-based. Branch names must describe
the work, not the person or tool that created them.

## Allowed names

- `main` is the stable integration branch.
- `feature/<short-purpose>` is for active product work.
- `fix/<short-purpose>` is for a bounded correction.
- `experiment/<short-purpose>` is temporary and must be deleted or promoted
  when the experiment ends.
- `release/<version>` is used only while preparing a release.

Do not use agent, vendor, username, or machine prefixes. Do not keep remote
backup or validation branches after their result is integrated. Before deleting
unique historical work, preserve it in a verified local Git bundle.

Coursework and private research under `research/` are local-only unless the
owner explicitly approves publication.
