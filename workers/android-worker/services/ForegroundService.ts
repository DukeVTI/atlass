/**
 * ForegroundService.ts
 * --------------------
 * Keeps the Atlas daemon alive on Android even when the app is backgrounded
 * or the screen is locked.
 *
 * Architecture:
 *   Android kills any app process that has no foreground service after ~1 minute
 *   in the background. A foreground service requires a persistent, non-dismissable
 *   notification — this is the "price" Android charges for background execution.
 *
 * Implementation:
 *   expo-notifications alone is NOT sufficient — it cannot prevent JS suspension.
 *   We use expo-task-manager with a BACKGROUND_FETCH task to periodically wake
 *   the JS runtime, combined with a high-priority persistent notification channel
 *   to signal Android that this process must not be killed.
 *
 * NOTE: For a fully native foreground service (guaranteed to survive aggressive
 * battery optimization like Doze mode), a native Expo module or a bare React
 * Native module (react-native-foreground-service) is recommended for production.
 * This implementation is the best achievable within managed Expo workflow.
 */

import * as TaskManager from 'expo-task-manager';
import * as BackgroundFetch from 'expo-background-fetch';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

// ─── Constants ────────────────────────────────────────────────────────────────

const ATLAS_BG_TASK = 'ATLAS_DAEMON_BACKGROUND_FETCH';
const ATLAS_CHANNEL_ID = 'atlas-daemon';
const ATLAS_NOTIFICATION_ID = 'atlas-persistent';

// ─── Notification Channel (Android only) ─────────────────────────────────────
// A dedicated LOW_PRIORITY channel keeps the notification non-intrusive
// while still marking this as a foreground process to Android.
export async function setupNotificationChannel(): Promise<void> {
  if (Platform.OS !== 'android') return;

  await Notifications.setNotificationChannelAsync(ATLAS_CHANNEL_ID, {
    name: 'Atlas Daemon',
    importance: Notifications.AndroidImportance.LOW,
    vibrationPattern: [0],
    lightColor: '#00FF9D',
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.SECRET,
    bypassDnd: false,
    showBadge: false,
  });
}

// ─── Background Fetch Task ────────────────────────────────────────────────────
// Registered at module scope — this is required by expo-task-manager.
// Android will wake this task periodically (minimum ~15 min interval),
// giving the JS runtime a chance to re-establish the WebSocket if needed.
TaskManager.defineTask(ATLAS_BG_TASK, async () => {
  console.log('[Atlas ForegroundService] Background wake-up — checking connection.');
  // The actual reconnect logic lives in useAtlasDaemon.
  // This task just keeps the process warm and signals Android we need uptime.
  return BackgroundFetch.BackgroundFetchResult.NewData;
});

// ─── Public API ───────────────────────────────────────────────────────────────

export async function startForegroundService(statusText: string = 'Daemon active'): Promise<void> {
  // 1. Request notification permissions
  const { status } = await Notifications.requestPermissionsAsync();
  if (status !== 'granted') {
    console.warn('[Atlas ForegroundService] Notification permission denied — foreground service cannot start.');
    return;
  }

  // 2. Set up the notification channel (Android)
  await setupNotificationChannel();

  // 3. Post the persistent notification
  // Using identifier lets us update it in-place without a flicker.
  await Notifications.scheduleNotificationAsync({
    identifier: ATLAS_NOTIFICATION_ID,
    content: {
      title: '⬡  Atlas Daemon',
      body: statusText,
      sticky: true,
      autoDismiss: false,
      priority: Notifications.AndroidNotificationPriority.LOW,
      // Android-specific: keeps this in the "Ongoing" section
      ...(Platform.OS === 'android' && {
        categoryIdentifier: 'service',
      }),
    },
    trigger: null,
  });

  // 4. Register background fetch — minimum interval Android allows is 15 min
  try {
    await BackgroundFetch.registerTaskAsync(ATLAS_BG_TASK, {
      minimumInterval: 15 * 60,   // 15 minutes
      stopOnTerminate: false,      // Survive app swipe-close
      startOnBoot: true,           // Re-register after device restart
    });
    console.log('[Atlas ForegroundService] Background fetch task registered.');
  } catch (e) {
    // Task may already be registered — that's fine
    console.debug('[Atlas ForegroundService] Background task registration note:', e);
  }

  console.log('[Atlas ForegroundService] Started with status:', statusText);
}

export async function updateForegroundNotification(statusText: string): Promise<void> {
  // Dismiss existing and repost with updated body
  // (expo-notifications does not support in-place content update via identifier alone)
  await Notifications.dismissNotificationAsync(ATLAS_NOTIFICATION_ID);
  await startForegroundService(statusText);
}

export async function stopForegroundService(): Promise<void> {
  await Notifications.dismissNotificationAsync(ATLAS_NOTIFICATION_ID);
  try {
    await BackgroundFetch.unregisterTaskAsync(ATLAS_BG_TASK);
  } catch (_) {
    // Task may not have been registered
  }
  console.log('[Atlas ForegroundService] Stopped.');
}
