/** Request-token helper for stale-response tests. No network. */

export function createScopeTokens() {
  const tokens = Object.create(null);
  return {
    next(scope) {
      tokens[scope] = (tokens[scope] || 0) + 1;
      return tokens[scope];
    },
    live(scope, tok) {
      return tokens[scope] === tok;
    },
    invalidate(scopes) {
      for (const s of scopes) tokens[s] = (tokens[s] || 0) + 1;
    },
  };
}
