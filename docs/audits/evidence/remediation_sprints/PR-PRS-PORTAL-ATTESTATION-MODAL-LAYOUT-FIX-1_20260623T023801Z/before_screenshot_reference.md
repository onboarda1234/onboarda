# Before Screenshot Reference

The original issue screenshot was provided in the task prompt, not as a local image artifact.

Observed defect from the provided screenshot:
- URL: `https://staging.regmind.co/portal`
- Periodic Review Attestation modal open.
- Left portal sidebar visually overlapped the attestation modal.
- Modal title and question content were clipped on the left.
- Close button remained visible, but the modal alignment was wrong.

Root cause confirmed during local patched-browser smoke setup: the modal lived inside the `.app` stacking context, which has a lower z-index than the fixed portal sidebar. A modal z-index inside that stacking context could not reliably sit above the sidebar.
