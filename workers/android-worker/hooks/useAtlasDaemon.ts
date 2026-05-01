/**
 * useAtlasDaemon
 * --------------
 * Core WebSocket connection to the Atlas VPS Brain.
 * Identifies as a mobile_worker, receives tool commands,
 * dispatches them to the local tool registry, and returns results.
 *
 * Protocol matches the existing PC Worker exactly:
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
  const intentionalClose = useRef(false);

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
      ws.current?.send(JSON.stringify({
        task_id,
        status: 'success',
        result,
      }));
      console.log(`[Atlas] Tool ${tool} completed.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      ws.current?.send(JSON.stringify({
        task_id,
        status: 'error',
        result: `Tool error: ${message}`,
      }));
      console.error(`[Atlas] Tool ${tool} failed:`, message);
    }
  }, []);

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    const uri = `${vpsUrl}/ws?token=${workerToken}`;
    console.log(`[Atlas] Connecting to ${uri}`);
    setStatus('connecting');

    try {
      ws.current = new WebSocket(uri);

      ws.current.onopen = () => {
        console.log('[Atlas] Connected to VPS Brain.');
        ws.current?.send(JSON.stringify({
          type: 'identity',
          worker_type: 'mobile',
          name: workerName,
        }));
        setStatus('connected');
        reconnectDelay.current = 3000;
      };

      ws.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as AtlasTask;
          if (data.task_id && data.tool) {
            executeTask(data);
          }
        } catch (e) {
          console.warn('[Atlas] Failed to parse incoming message:', e);
        }
      };

      ws.current.onerror = (e) => {
        console.error('[Atlas] WebSocket error:', e);
        setStatus('error');
      };

      ws.current.onclose = () => {
        setStatus('disconnected');
        if (!intentionalClose.current) {
          console.log(`[Atlas] Reconnecting in ${reconnectDelay.current / 1000}s...`);
          if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
          reconnectTimer.current = setTimeout(() => {
            reconnectDelay.current = Math.min(reconnectDelay.current * 2, 60000);
            connect();
          }, reconnectDelay.current);
        }
      };
    } catch (e) {
      console.error('[Atlas] Failed to create WebSocket:', e);
      setStatus('error');
    }
  }, [vpsUrl, workerToken, workerName, executeTask]);

  const disconnect = useCallback(() => {
    intentionalClose.current = true;
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    ws.current?.close();
    setStatus('disconnected');
  }, []);

  useEffect(() => {
    intentionalClose.current = false;
    connect();
    return () => {
      intentionalClose.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  return { status, lastTool, connect, disconnect };
}
