# Security Policy

## Report a vulnerability or confidential exposure privately

This is a public repository. **Do not disclose vulnerability details or confidential material in a public issue, pull request, discussion, commit message, screenshot, CI log, or chat.** This includes real borrower or customer names and figures, personal data, confidential documents, API keys, tokens, passwords, and other secrets.

The intended reporting channel is GitHub's **private vulnerability reporting** for this repository: open the repository's **Security** tab and choose **Report a vulnerability**. A maintainer must enable this repository setting for the private form to be available; a pull request cannot enable it.

If the private reporting option is unavailable, do not create a public issue containing the details. Use a private contact channel the maintainer has already provided, or ask the maintainer—in a message containing no sensitive detail—to enable private vulnerability reporting. The public issue tracker must not be used as a fallback disclosure channel.

### What to include in a private report

Include the affected commit or release, the security impact, steps to reproduce using synthetic data, and any practical mitigation. Do not attach a real deal, customer document, live credential, or a copy of leaked data. Identify the kind of secret or record involved without reproducing its contents.

### If a secret or confidential data was exposed

Revoke or rotate an exposed credential promptly. Tell the maintainer privately that material was exposed; do not paste the material itself. The maintainer will assess removal from the branch and whether repository history or affected parties require further action. Removing material from the latest commit does not make a leaked credential safe again.

## Public bug reports

All issues in this repository are public. Use synthetic examples only. Do not include real data, personal information, confidential documents, or secrets. Report vulnerabilities and suspected data exposure through the private channel described above, not through a bug report.
