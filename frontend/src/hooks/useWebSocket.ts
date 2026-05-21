import { useEffect, useRef, useState, useCallback } from "react";

interface WebSocketMessage {
  event_id: string;
  type: string;
  board_id: string;
  ticket_id: string;
  ticket_key: string;
  actor_id: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
}

// New message types for ping-pong and error handling
interface PongMessage {
  type: "pong";
  timestamp: number;
  connection_id: string;
}

interface ErrorMessage {
  error: string;
  message: string;
  retry_allowed: boolean;
}

interface SystemDegradationMessage extends WebSocketMessage {
  type: "system_degradation";
  payload: {
    message: string;
    reason: string;
    retry_count: number;
    error: string;
  };
}

interface UseWebSocketOptions {
  boardId: string;
  token: string;
  onMessage?: (message: WebSocketMessage) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Event) => void;
  onSystemDegradation?: (message: SystemDegradationMessage) => void;
  connectionTimeout?: number; // milliseconds, default 15000
  pingInterval?: number; // milliseconds, default 30000
}

interface ConnectionQuality {
  status: "excellent" | "good" | "poor" | "disconnected";
  latency: number | null; // ms
  lastPingTime: number | null;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  isConnecting: boolean;
  lastMessage: WebSocketMessage | null;
  error: Event | ErrorMessage | null;
  connectionQuality: ConnectionQuality;
  reconnectAttempts: number;
  connect: () => void;
  disconnect: () => void;
  sendPing: () => void;
}

export function useWebSocket({
  boardId,
  token,
  onMessage,
  onConnect,
  onDisconnect,
  onError,
  onSystemDegradation,
  connectionTimeout = 15000,
  pingInterval = 30000,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null);
  const [error, setError] = useState<Event | ErrorMessage | null>(null);
  const [connectionQuality, setConnectionQuality] = useState<ConnectionQuality>({
    status: "disconnected",
    latency: null,
    lastPingTime: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const connectionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastPingTimeRef = useRef<number | null>(null);

  // Keep callbacks in refs so WS event handlers always call the latest version
  const onMessageRef = useRef(onMessage);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const onSystemDegradationRef = useRef(onSystemDegradation);

  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);
  useEffect(() => { onConnectRef.current = onConnect; }, [onConnect]);
  useEffect(() => { onDisconnectRef.current = onDisconnect; }, [onDisconnect]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { onSystemDegradationRef.current = onSystemDegradation; }, [onSystemDegradation]);

  const maxReconnectAttempts = 5;
  const baseReconnectDelay = 1000;

  // Cleanup function for connection resources
  const cleanupConnection = useCallback(() => {
    if (connectionTimeoutRef.current) {
      clearTimeout(connectionTimeoutRef.current);
      connectionTimeoutRef.current = null;
    }
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  // Send ping message to server
  const sendPing = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const pingTime = Date.now();
      lastPingTimeRef.current = pingTime;

      try {
        wsRef.current.send(JSON.stringify({ type: "ping" }));
        setConnectionQuality(prev => ({
          ...prev,
          lastPingTime: pingTime,
        }));
      } catch (err) {
        console.error("Failed to send ping:", err);
      }
    }
  }, []);

  // Handle different message types
  const handleMessage = useCallback((rawMessage: string) => {
    try {
      const data = JSON.parse(rawMessage);

      if (data.type === "pong") {
        // Handle pong response for connection quality
        const pongMessage = data as PongMessage;
        const pingTime = lastPingTimeRef.current;

        if (pingTime) {
          const latency = Date.now() - pingTime;
          const status: ConnectionQuality["status"] =
            latency < 100 ? "excellent" :
            latency < 500 ? "good" :
            latency < 5000 ? "poor" : "poor";

          setConnectionQuality(prev => ({
            ...prev,
            latency,
            status: isConnected ? status : "disconnected",
          }));
        }
        return;
      }

      if (data.error) {
        // Handle structured error messages from server
        const errorMessage = data as ErrorMessage;
        setError(errorMessage);
        onErrorRef.current?.(new Event("websocket-error"));

        if (!errorMessage.retry_allowed) {
          // Don't attempt reconnection for non-retryable errors
          reconnectAttemptsRef.current = maxReconnectAttempts;
        }
        return;
      }

      if (data.type === "system_degradation") {
        // Handle system degradation messages
        const degradationMessage = data as SystemDegradationMessage;
        onSystemDegradationRef.current?.(degradationMessage);
        return;
      }

      // Regular event message
      const message = data as WebSocketMessage;
      setLastMessage(message);
      onMessageRef.current?.(message);

    } catch (err) {
      console.error("WebSocket message parse error:", err, "Raw:", rawMessage);
    }
  }, [isConnected, onErrorRef, onMessageRef, onSystemDegradationRef, maxReconnectAttempts]);

  const connect = useCallback(() => {
    if (!boardId || !token) {
      console.log('[WebSocket] Cannot connect - missing boardId or token');
      return;
    }

    // Clean up any existing connection
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING) {
        console.log('[WebSocket] Already connected or connecting, skipping...');
        return;
      }

      try {
        wsRef.current.close(1000, "Reconnecting");
      } catch (e) {
        // Ignore close errors
      }
      wsRef.current = null;
    }

    cleanupConnection();
    setIsConnecting(true);
    setError(null);
    setConnectionQuality(prev => ({ ...prev, status: "disconnected" }));

    // Connection timeout handler
    connectionTimeoutRef.current = setTimeout(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.CONNECTING) {
        wsRef.current.close(1000, "Connection timeout");

        const timeoutError: ErrorMessage = {
          error: "connection_timeout",
          message: "Connection timeout - check network",
          retry_allowed: true,
        };

        setError(timeoutError);
        setIsConnecting(false);
        setConnectionQuality(prev => ({ ...prev, status: "disconnected" }));

        onErrorRef.current?.(new Event("connection-timeout"));
      }
    }, connectionTimeout);

    // Create WebSocket connection
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsBase = import.meta.env.VITE_WS_URL || `${wsProtocol}//${window.location.host}`;
    const wsUrl = new URL(`/ws/boards/${boardId}`, wsBase);
    wsUrl.searchParams.set("token", token);

    const ws = new WebSocket(wsUrl.toString());
    wsRef.current = ws;

    ws.onopen = () => {
      cleanupConnection();
      setIsConnected(true);
      setIsConnecting(false);
      setError(null);
      reconnectAttemptsRef.current = 0;

      setConnectionQuality({
        status: "good",
        latency: null,
        lastPingTime: null,
      });

      // Start ping interval
      pingIntervalRef.current = setInterval(sendPing, pingInterval);

      // Send initial ping
      setTimeout(sendPing, 1000);

      onConnectRef.current?.();
    };

    ws.onmessage = (event) => {
      handleMessage(event.data);
    };

    ws.onclose = (event) => {
      cleanupConnection();
      setIsConnected(false);
      setIsConnecting(false);
      setConnectionQuality(prev => ({ ...prev, status: "disconnected" }));

      onDisconnectRef.current?.();

      // Enhanced error messaging for authentication issues
      if (event.code === 1006) {
        const tokenPreview = token ? token.substring(0, 8) + '...' : 'no token';
        console.error('[WebSocket] Connection closed with 1006 - authentication issue:', {
          tokenUsed: token ? 'token present' : 'no token',
          tokenPreview,
          tokenLength: token?.length,
          tokenSource: 'auth store (useAuth)',
          boardId,
          reason: event.reason || 'Unknown',
          localStorageToken: localStorage.getItem('projecthub.token')?.substring(0, 8) + '...'
        });

        // Check if token looks like a jarwis-backend token (common issue)
        if (token?.startsWith('1c7f53fb')) {
          console.error('[WebSocket] ERROR: Using jarwis-backend token instead of user token!');
          console.error('[WebSocket] This token belongs to backend agent, not for frontend use.');
          console.error('[WebSocket] Clear localStorage and re-login with Admin token.');
        }
      }

      // Auto-reconnect logic with exponential backoff
      if (!event.wasClean && reconnectAttemptsRef.current < maxReconnectAttempts) {
        const delay = baseReconnectDelay * Math.pow(2, reconnectAttemptsRef.current);
        reconnectAttemptsRef.current += 1;

        console.log(
          `WebSocket reconnecting (${reconnectAttemptsRef.current}/${maxReconnectAttempts}) in ${delay}ms...`,
          `Code: ${event.code}, Reason: ${event.reason || 'Unknown'}`
        );

        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      } else if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
        console.error("WebSocket max reconnection attempts reached");
        const maxRetriesError: ErrorMessage = {
          error: "max_retries_exceeded",
          message: "Connection failed after multiple attempts",
          retry_allowed: true,
        };
        setError(maxRetriesError);
      }
    };

    ws.onerror = (event) => {
      console.error("WebSocket error:", event);
      setError(event);
      setIsConnecting(false);
      setConnectionQuality(prev => ({ ...prev, status: "disconnected" }));
      onErrorRef.current?.(event);
    };
  }, [
    boardId,
    token,
    connectionTimeout,
    pingInterval,
    cleanupConnection,
    sendPing,
    handleMessage,
    maxReconnectAttempts,
    baseReconnectDelay,
    onConnectRef,
    onDisconnectRef,
    onErrorRef,
  ]);

  const disconnect = useCallback(() => {
    cleanupConnection();

    if (wsRef.current) {
      wsRef.current.close(1000, "Client disconnect");
      wsRef.current = null;
    }

    setIsConnected(false);
    setIsConnecting(false);
    setConnectionQuality(prev => ({ ...prev, status: "disconnected" }));
    setError(null);
    reconnectAttemptsRef.current = 0;
  }, [cleanupConnection]);

  // Token change detection is now handled by the auto-connect effect
  // We don't need a separate effect for this

  // Auto-connect effect - only connect if we have both boardId and token
  useEffect(() => {
    if (boardId && token) {
      connect();
    }

    return () => {
      disconnect();
    };
  }, [boardId, token, connect, disconnect]);

  return {
    isConnected,
    isConnecting,
    lastMessage,
    error,
    connectionQuality,
    reconnectAttempts: reconnectAttemptsRef.current,
    connect,
    disconnect,
    sendPing,
  };
}
