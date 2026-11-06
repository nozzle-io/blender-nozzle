# Latest publication

A successful main publication uploads the three addon packages, checks the
published asset set and notes, downloads each package and compares SHA256 with
the local artifact, then moves only `refs/tags/latest` to the workflow commit.
The remote tag commit is read back before success is reported.

The git push leases the previously observed tag ref object. A changed tag is
an error, not permission for a blind overwrite. Main workflow runs serialize
without automatic cancellation; a stale rerun is rejected against current main
before release mutation. PR runs remain cancellable.

Versioned releases do not move tags. Dry-run dispatches print the verification
and movement steps without performing them. The release job also requires the
publisher's script tests, not only platform packaging jobs.

This is not an atomic release transaction: existing delete-first asset replacement
and early notes editing can leave an incomplete release after an upload failure
or explicit cancellation. Such a failure must not advance an existing latest tag.
Creating a previously absent release may create its initial tag through GitHub;
the post-upload step still verifies and sets the exact successful workflow commit.

Do not repair historical tags manually using current main. The next successful
real publication updates the assets, notes and tag together. Publisher tests use
controlled subprocess responses and files to exercise ordering and failure paths;
only a real main run supplies GitHub permission/upload/tag evidence.
