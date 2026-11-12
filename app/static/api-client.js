export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function createJsonClient({ fetchImpl = globalThis.fetch, onSuccess = () => {}, onError = () => {} } = {}) {
  return async function fetchJson(url, options = {}) {
    try {
      const response = await fetchImpl(url, options);
      if (!response.ok) {
        let detail;
        try {
          const body = await response.json();
          if (typeof body.detail === 'string') detail = body.detail;
        } catch { /* Proxies can return non-JSON error pages. */ }
        throw new ApiError(response.status, detail);
      }
      const data = await response.json();
      onSuccess(url, options);
      return data;
    } catch (error) {
      onError(error, url, options);
      throw error;
    }
  };
}
