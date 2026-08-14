export function attachWatchlistEventSource(options) {
  const {
    eventUrl,
    onItems,
    onStatus = () => {},
    onError = () => {},
    withCredentials = false,
  } = options;

  let source = null;
  let reconnectTimer = null;

  function connect() {
    onStatus({ mode: "connecting", eventUrl });
    source = new EventSource(eventUrl, { withCredentials });

    source.addEventListener("watchlist", (event) => {
      try {
        const message = JSON.parse(event.data);
        const payload = message.payload || {};
        onItems(payload.items || [], message);
        onStatus({
          mode: "live",
          sequence: payload.sequence,
          publishedAt: payload.published_at,
          pollSeconds: message.poll_seconds,
          upstreamError: message.error,
        });
      } catch (error) {
        onError(error);
      }
    });

    source.onerror = (error) => {
      onStatus({ mode: "reconnecting", eventUrl });
      onError(error);
      cleanup();
      reconnectTimer = setTimeout(connect, 3000);
    };
  }

  function cleanup() {
    if (source) {
      source.close();
      source = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  connect();
  return cleanup;
}

export function replacePollingStart(startPolling, stopPolling, realtimeOptions) {
  let detach = null;

  return function startRealtime() {
    stopPolling();
    detach = attachWatchlistEventSource(realtimeOptions);
    return () => {
      if (detach) {
        detach();
        detach = null;
      }
      startPolling();
    };
  };
}
