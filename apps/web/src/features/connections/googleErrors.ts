/** Plain-language copy for the broker's OAuth return codes (docs/capability-broker.md). */
export function googleErrorText(code: string | null): string {
  switch (code) {
    case 'OAUTH_DENIED':
      return 'Access was not granted on the Google screen. Try again and allow the requested access.';
    case 'OAUTH_STATE_INVALID':
      return 'The sign-in link expired or was opened in another browser. Start again from this page.';
    case 'OAUTH_SCOPE_MISSING':
      return 'Some requested access was not granted. Try again and allow every requested permission.';
    case 'OAUTH_CODE_INVALID':
      return 'Google did not accept the sign-in. Start again.';
    default:
      return 'Google sign-in failed. Start again from this page.';
  }
}
