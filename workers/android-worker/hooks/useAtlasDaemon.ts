/**
 * useAtlasDaemon
 * --------------
 * Core WebSocket connection to the Atlas VPS Brain.
 * Identifies as a mobile_worker, receives tool commands,
 * dispatches them to the local tool registry, and returns results.
 *
 * Heartbeat Protocol:
 *   - Server sends {"type": "ping"} every 30s.
 *   - This hook replies with {"type": "pong"} immediately.
 *   - Client also sends its own ping every 25s as a belt-and-suspenders
 *     measure against aggressive carrier NAT timeouts.
 *
 * Task Protocol (unchanged):
 *   RECEIVE: { task_id, tool, kwargs }
 *   SEND:    { task_id, status, result }
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { TOOL_REGISTRY } from '../tools';

export type DaemonStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface AtlasTask {
  task_id: string;
  tool: string;
  kwargs: Record<string, unknown>;
}

interface UseDaemonOptions {
  vpsUrl: string;
  workerToken: string;
  workerName?: string;
}

// Belt-and-suspenders: client sends a keepalive ping every 25s
// even if the server doesn't ask for one. This prevents NAT
// middleboxes from dropping the TCP connection during silence.
const CLIENT_PING_INTERVAL_MS = 25_000;

export function useAtlasDaemon({
  vpsUrl,
  workerToken,
  workerName = 'duke-android',
}: UseDaemonOptions) {
  const ws = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<DaemonStatus>('disconnected');
  const [lastTool, setLastTool] = useState<string | null>(null);
  const reconnectDelay = useRef(3000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionalClose = useRef(false);

  // ── Cleanup helpers ──────────────────────────────────────────────────────
  const clearPingTimer = useCallback(() => {
    if (pingTimer.current) {
      clearInterval(pingTimer.current);
      pingTimer.current = null;
    }
  }, []);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
  }, []);

  // ── Tool execution ───────────────────────────────────────────────────────
  const executeTask = useCallback(async (task: AtlasTask) => {
    const { task_id, tool, kwargs } = task;
    setLastTool(tool);

    console.log(`[Atlas] Executing tool: ${tool}`, kwargs);

    const handler = TOOL_REGISTRY[tool];
    if (!handler) {
      ws.current?.send(JSON.stringify({
        task_id,
        status: 'error',
        result: `Unknown tool: ${tool}`,
      }));
      return;
    }

    try {
      const result = await handler(kwargs);
      ws.current?.send(JSON.stringify({ task_id, status: 'success', result }));
      console.log(`[Atlas] Tool ${tool} completed.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      ws.current?.send(JSON.stringify({ task_id, status: 'error', result: `Tool error: ${message}` }));
      console.error(`[Atlas] Tool ${tool} failed:`, message);
    }
  }, []);

  // ── Connection ───────────────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    const uri = `${vpsUrl}/ws?token=${workerToken}`;
    console.log(`[Atlas] Connecting to ${uri}`);
    setStatus('connecting');

    try {
      ws.current = new WebSocket(uri);

      ws.current.onopen = () => {
        console.log('[Atlas] Connected to VPS Brain.');

        // Send identity handshake immediately
        ws.current?.send(JSON.stringify({
          type: 'identity',
          worker_type: 'mobile',
          name: workerName,
        }));

        setStatus('connected');
        reconnectDelay.current = 3000;

        // Start client-side keepalive ping
        clearPingTimer();
        pingTimer.current = setInterval(() => {
          if (ws.current?.readyState === WebSocket.OPEN) {
            ws.current.send(JSON.stringify({ type: 'ping' }));
            console.debug('[Atlas] Sent keepalive ping.');
          }
        }, CLIENT_PING_INTERVAL_MS);
      };

      ws.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string);

          // Server heartbeat — reply with pong immediately
          if (data.type === 'ping') {
            ws.current?.send(JSON.stringify({ type: 'pong' }));
            console.debug('[Atlas] Received ping → sent pong.');
            return;
          }

          // Tool execution task
          if (data.task_id && data.tool) {
            executeTask(data as AtlasTask);
          }
        } catch (e) {
          console.warn('[Atlas] Failed to parse incoming message:', e);
        }
      };

      ws.current.onerror = (e) => {
        console.error('[Atlas] WebSocket error:', e);
        setStatus('error');
      };

      ws.current.onclose = (event) => {
        clearPingTimer();
        setStatus('disconnected');
        console.log(`[Atlas] Connection closed — code: ${event.code}, reason: ${event.reason || 'none'}`);

        if (!intentionalClose.current) {
          console.log(`[Atlas] Reconnecting in ${reconnectDelay.current / 1000}s…`);
          clearReconnectTimer();
          reconnectTimer.current = setTimeout(() => {
            reconnectDelay.current = Math.min(reconnectDelay.current * 2, 60_000);
            connect();
          }, reconnectDelay.current);
        }
      };
    } catch (e) {
      console.error('[Atlas] Failed to create WebSocket:', e);
      setStatus('error');
    }
  }, [vpsUrl, workerToken, workerName, executeTask, clearPingTimer, clearReconnectTimer]);

  const disconnect = useCallback(() => {
    intentionalClose.current = true;
    clearPingTimer();
    clearReconnectTimer();
    ws.current?.close(1000, 'User disconnected');
    setStatus('disconnected');
  }, [clearPingTimer, clearReconnectTimer]);

  useEffect(() => {
    intentionalClose.current = false;
    connect();
    return () => {
      intentionalClose.current = true;
      clearPingTimer();
      clearReconnectTimer();
      ws.current?.close();
    };
  }, [connect, clearPingTimer, clearReconnectTimer]);

  return { status, lastTool, connect, disconnect };
}
