/**
 * notifications.ts — Notification tools
 *
 * Two capabilities:
 * 1. Send a local push notification to Duke's phone (Atlas → phone)
 * 2. Read recent notification history (requires NotificationListenerService)
 *
 * Package: expo-notifications
 * For reading other apps' notifications: react-native-notification-listener
 * (requires enabling Notification Access in Android settings)
 */

import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

// Configure how notifications appear when app is foregrounded
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// ─── Send notification to phone ───────────────────────────────────────────────

interface PushNotificationArgs {
  title: string;
  body: string;
  urgent?: boolean;
}

export async function pushNotification(args: Record<string, unknown>): Promise<string> {
  const { title, body, urgent = false } = args as PushNotificationArgs;

  if (!title || !body) return 'Error: title and body required.';

  const { status } = await Notifications.requestPermissionsAsync();
  if (status !== 'granted') return 'Notification permission denied.';

  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
      sound: urgent ? 'default' : undefined,
      priority: urgent
        ? Notifications.AndroidNotificationPriority.MAX
        : Notifications.AndroidNotificationPriority.DEFAULT,
    },
    trigger: null, // fire immediately
  });

  return `Notification sent: "${title}"`;
}

// ─── Read notifications from other apps ───────────────────────────────────────

interface ReadNotificationsArgs {
  limit?: number;
  appFilter?: string; // filter by app package name e.g. "com.whatsapp"
}

export async function readNotifications(args: Record<string, unknown>): Promise<object> {
  if (Platform.OS !== 'android') {
    return { error: 'Notification reading is Android-only.' };
  }

  const { limit = 20, appFilter } = args as ReadNotificationsArgs;

  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const RNNotificationListener = require('react-native-notification-listener');

    const hasPermission = await RNNotificationListener.getPermissionStatus();
    if (hasPermission !== 'authorized') {
      return {
        error: 'Notification listener not enabled. Duke must go to: Settings → Apps → Special app access → Notification access → Enable for Atlas.',
        permission_status: hasPermission,
      };
    }

    const notifications = await RNNotificationListener.getNotifications({
      limit,
      app: appFilter,
    });

    return {
      count: notifications.length,
      notifications: notifications.map((n: {
        app: string;
        title: string;
        text: string;
        time: number;
      }) => ({
        app: n.app,
        title: n.title,
        text: n.text,
        time: new Date(n.time).toISOString(),
      })),
    };
  } catch (err) {
    return {
      error: `Notification listener module not available: ${(err as Error).message}. Install react-native-notification-listener.`,
    };
  }
}
