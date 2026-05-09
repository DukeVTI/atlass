/**
 * Atlas Android Worker — App.tsx
 * --------------------------------
 * Minimal status screen. The real work happens in the daemon (useAtlasDaemon).
 * This UI just shows connection state and the last tool Atlas ran.
 *
 * The app runs as a foreground service — it stays alive in the background
 * even when Duke locks his phone.
 */

import React, { useEffect } from 'react';
import {
  View, Text, StyleSheet, SafeAreaView,
  StatusBar, TouchableOpacity,
} from 'react-native';
import { useAtlasDaemon, DaemonStatus } from './hooks/useAtlasDaemon';
import {
  startForegroundService,
  updateForegroundNotification,
  stopForegroundService,
} from './services/ForegroundService';

const VPS_WS_URL = process.env.EXPO_PUBLIC_VPS_WS_URL ?? 'ws://213.136.65.25:8000';
const WORKER_TOKEN = process.env.EXPO_PUBLIC_WORKER_TOKEN ?? 'change_me_before_deploy';

const STATUS_COLORS: Record<DaemonStatus, string> = {
  connected: '#00FF9D',
  connecting: '#F0A500',
  disconnected: '#555',
  error: '#FF3B3B',
};

const STATUS_LABELS: Record<DaemonStatus, string> = {
  connected: 'CONNECTED',
  connecting: 'CONNECTING...',
  disconnected: 'OFFLINE',
  error: 'ERROR',
};

export default function App() {
  const { status, lastTool, connect, disconnect } = useAtlasDaemon({
    vpsUrl: VPS_WS_URL,
    workerToken: WORKER_TOKEN,
    workerName: 'duke-android',
  });

  // Keep foreground service in sync with connection status
  useEffect(() => {
    if (status === 'connected') {
      startForegroundService('Atlas Daemon — Connected to VPS');
    } else if (status === 'connecting') {
      updateForegroundNotification('Atlas Daemon — Connecting...');
    } else {
      updateForegroundNotification('Atlas Daemon — Offline');
    }
  }, [status]);

  // Cleanup on unmount
  useEffect(() => {
    return () => { stopForegroundService(); };
  }, []);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#0A0A0A" />

      <View style={styles.center}>
        {/* Atlas logo / indicator */}
        <View style={[styles.orb, { borderColor: STATUS_COLORS[status] + '44' }]}>
          <View style={[styles.orbInner, { backgroundColor: STATUS_COLORS[status] }]} />
        </View>

        <Text style={styles.title}>ATLAS</Text>
        <Text style={styles.subtitle}>Mobile Worker</Text>

        {/* Status */}
        <View style={[styles.statusBadge, { borderColor: STATUS_COLORS[status] + '44' }]}>
          <View style={[styles.statusDot, { backgroundColor: STATUS_COLORS[status] }]} />
          <Text style={[styles.statusText, { color: STATUS_COLORS[status] }]}>
            {STATUS_LABELS[status]}
          </Text>
        </View>

        {/* Last tool executed */}
        {lastTool && (
          <View style={styles.lastTool}>
            <Text style={styles.lastToolLabel}>LAST TOOL</Text>
            <Text style={styles.lastToolName}>{lastTool}</Text>
          </View>
        )}

        {/* Active tools */}
        <View style={styles.toolGrid}>
          {[
            'speak', 'get_location', 'read_sms',
            'send_sms', 'push_notification', 'get_device_stats',
            'read_contacts', 'read_notifications',
          ].map(tool => (
            <View key={tool} style={styles.toolPill}>
              <Text style={styles.toolPillText}>{tool}</Text>
            </View>
          ))}
        </View>
      </View>

      {/* Manual disconnect/reconnect for debugging */}
      <View style={styles.footer}>
        {status === 'connected' ? (
          <TouchableOpacity style={styles.btnDanger} onPress={disconnect}>
            <Text style={styles.btnDangerText}>Disconnect</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={styles.btnPrimary} onPress={connect}>
            <Text style={styles.btnPrimaryText}>Reconnect</Text>
          </TouchableOpacity>
        )}
        <Text style={styles.footerNote}>
          This app must stay installed to keep the daemon alive.
        </Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#0A0A0A' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  orb: {
    width: 80, height: 80, borderRadius: 40,
    borderWidth: 1.5, alignItems: 'center',
    justifyContent: 'center', marginBottom: 20,
  },
  orbInner: { width: 24, height: 24, borderRadius: 12 },
  title: {
    color: '#FFF', fontSize: 28,
    fontWeight: '700', letterSpacing: 8,
    marginBottom: 4,
  },
  subtitle: { color: '#444', fontSize: 12, letterSpacing: 2, marginBottom: 28 },
  statusBadge: {
    flexDirection: 'row', alignItems: 'center',
    gap: 8, paddingHorizontal: 16, paddingVertical: 8,
    borderWidth: 1, borderRadius: 20, marginBottom: 24,
  },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: { fontSize: 12, fontWeight: '700', letterSpacing: 1.5 },
  lastTool: { alignItems: 'center', marginBottom: 20 },
  lastToolLabel: { color: '#333', fontSize: 10, letterSpacing: 2, marginBottom: 4 },
  lastToolName: { color: '#00FF9D', fontSize: 14, fontWeight: '600' },
  toolGrid: {
    flexDirection: 'row', flexWrap: 'wrap',
    gap: 6, justifyContent: 'center', marginTop: 8,
  },
  toolPill: {
    paddingHorizontal: 10, paddingVertical: 4,
    backgroundColor: '#111', borderRadius: 10,
    borderWidth: 0.5, borderColor: '#222',
  },
  toolPillText: { color: '#444', fontSize: 10 },
  footer: { padding: 24, alignItems: 'center', gap: 12 },
  btnPrimary: {
    paddingHorizontal: 28, paddingVertical: 12,
    backgroundColor: '#00FF9D', borderRadius: 8,
  },
  btnPrimaryText: { color: '#000', fontSize: 14, fontWeight: '700' },
  btnDanger: {
    paddingHorizontal: 28, paddingVertical: 12,
    backgroundColor: '#1A0808',
    borderWidth: 1, borderColor: '#FF3B3B44',
    borderRadius: 8,
  },
  btnDangerText: { color: '#FF3B3B', fontSize: 14, fontWeight: '600' },
  footerNote: { color: '#333', fontSize: 11, textAlign: 'center' },
});
