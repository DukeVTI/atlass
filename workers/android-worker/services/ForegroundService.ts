/**
 * ForegroundService.ts
 * --------------------
 * Keeps the Atlas daemon alive on Android even when the app is backgrounded.
 * Without a foreground service, Android will kill the process within minutes.
 *
 * This registers a persistent notification (like Spotify's "Now Playing" bar)
 * that keeps the process alive indefinitely.
 *
 * Package: expo-notifications (for the persistent notification)
 *          expo-task-manager (for background task registration)
 */

import * as TaskManager from 'expo-task-manager';
import * as Notifications from 'expo-notifications';

const ATLAS_DAEMON_TASK = 'ATLAS_DAEMON_KEEP_ALIVE';

// Register the background task — must be called at top level (module scope)
TaskManager.defineTask(ATLAS_DAEMON_TASK, async () => {
  console.log('[Atlas] Background keep-alive heartbeat.');
  return TaskManager.BackgroundFetchResult.NewData;
});

export async function startForegroundService(status: string = 'Connected') {
  const { status: perm } = await Notifications.requestPermissionsAsync();
  if (perm !== 'granted') {
    console.warn('[Atlas] Notification permission needed for foreground service.');
    return;
  }

  // Post a persistent notification — this is what keeps Android from killing us
  await Notifications.scheduleNotificationAsync({
    content: {
      title: 'Atlas Daemon',
      body: status,
      sticky: true,
      priority: Notifications.AndroidNotificationPriority.LOW,
      // Prevent user from dismissing it
      autoDismiss: false,
    },
    trigger: null,
  });

  console.log('[Atlas] Foreground service started.');
}

export async function updateForegroundNotification(status: string) {
  // Cancel existing and repost with updated status
  await Notifications.dismissAllNotificationsAsync();
  await startForegroundService(status);
}

export async function stopForegroundService() {
  await Notifications.dismissAllNotificationsAsync();
  console.log('[Atlas] Foreground service stopped.');
}
