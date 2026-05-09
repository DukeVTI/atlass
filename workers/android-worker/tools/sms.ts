/**
 * sms.ts — SMS read & send
 *
 * Reading SMS on Android requires the READ_SMS permission and
 * the react-native-get-sms-android package (bare workflow only).
 *
 * Sending SMS programmatically (no UI) requires SEND_SMS permission
 * and react-native-sms-android.
 *
 * Both are Android-only. iOS does not allow programmatic SMS access.
 */

import { PermissionsAndroid, Platform } from 'react-native';

// ─── Read SMS ─────────────────────────────────────────────────────────────────

interface ReadSMSArgs {
  box?: 'inbox' | 'sent' | 'draft';
  maxCount?: number;
  filter?: string; // filter by sender name/number
}

export async function readSMS(args: Record<string, unknown>): Promise<object> {
  if (Platform.OS !== 'android') {
    return { error: 'SMS reading is Android-only.' };
  }

  const { box = 'inbox', maxCount = 20, filter } = args as ReadSMSArgs;

  try {
    const granted = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.READ_SMS,
      {
        title: 'Atlas SMS Permission',
        message: 'Atlas needs to read SMS messages.',
        buttonPositive: 'Allow',
      }
    );

    if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
      return { error: 'READ_SMS permission denied.' };
    }

    // Dynamic import — only available in bare workflow with the native module installed
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const SmsAndroid = require('react-native-get-sms-android');

    return new Promise((resolve) => {
      const filterObj: Record<string, unknown> = {
        box,
        maxCount: maxCount as number,
      };
      if (filter) filterObj.address = filter;

      SmsAndroid.list(
        JSON.stringify(filterObj),
        (fail: string) => resolve({ error: `SMS read failed: ${fail}` }),
        (_count: number, smsList: string) => {
          const messages = JSON.parse(smsList) as Array<{
            address: string;
            body: string;
            date: number;
            type: number;
          }>;

          resolve({
            count: messages.length,
            messages: messages.map(m => ({
              from: m.address,
              body: m.body,
              date: new Date(m.date).toISOString(),
              type: m.type === 1 ? 'received' : 'sent',
            })),
          });
        }
      );
    });
  } catch (err) {
    return {
      error: `SMS module not available: ${(err as Error).message}. Ensure react-native-get-sms-android is installed.`,
    };
  }
}

// ─── Send SMS ─────────────────────────────────────────────────────────────────

interface SendSMSArgs {
  to: string;
  message: string;
}

export async function sendSMS(args: Record<string, unknown>): Promise<string> {
  if (Platform.OS !== 'android') {
    return 'SMS sending is Android-only.';
  }

  const { to, message } = args as SendSMSArgs;
  if (!to || !message) return 'Error: to and message are required.';

  try {
    const granted = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.SEND_SMS,
      {
        title: 'Atlas SMS Permission',
        message: 'Atlas needs permission to send SMS.',
        buttonPositive: 'Allow',
      }
    );

    if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
      return 'SEND_SMS permission denied.';
    }

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const SmsAndroid = require('react-native-sms-android');

    return new Promise((resolve) => {
      SmsAndroid.sms(
        to,
        message,
        'sendDirect', // sends without opening SMS app
        (_err: Error | null, _message: string) => {
          if (_err) resolve(`SMS send failed: ${_err.message}`);
          else resolve(`SMS sent to ${to}: "${message.slice(0, 50)}${message.length > 50 ? '...' : ''}"`);
        }
      );
    });
  } catch (err) {
    return `SMS module not available: ${(err as Error).message}. Ensure react-native-sms-android is installed.`;
  }
}
