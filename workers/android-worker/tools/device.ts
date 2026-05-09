/**
 * device.ts — Device stats tools
 * Battery, memory, device info, brightness, volume control.
 *
 * Packages: expo-battery, expo-device, expo-brightness,
 *           expo-system-ui, react-native
 */

import * as Battery from 'expo-battery';
import * as Device from 'expo-device';
import { Platform } from 'react-native';

// ─── Battery & System Stats ───────────────────────────────────────────────────

export async function getDeviceStats(_args: Record<string, unknown>): Promise<object> {
  try {
    const batteryLevel = await Battery.getBatteryLevelAsync();
    const batteryState = await Battery.getBatteryStateAsync();
    const isPowerSaver = await Battery.isLowPowerModeEnabledAsync();

    const stateLabel: Record<Battery.BatteryState, string> = {
      [Battery.BatteryState.CHARGING]: 'charging',
      [Battery.BatteryState.FULL]: 'full',
      [Battery.BatteryState.UNPLUGGED]: 'unplugged',
      [Battery.BatteryState.UNKNOWN]: 'unknown',
    };

    return {
      battery: {
        percent: Math.round(batteryLevel * 100),
        state: stateLabel[batteryState] ?? 'unknown',
        is_plugged_in: batteryState === Battery.BatteryState.CHARGING || batteryState === Battery.BatteryState.FULL,
        power_saver_on: isPowerSaver,
      },
      device: {
        name: Device.deviceName,
        brand: Device.brand,
        model: Device.modelName,
        os: `Android ${Device.osVersion}`,
        total_memory_gb: Device.totalMemory
          ? Math.round((Device.totalMemory / 1024 ** 3) * 10) / 10
          : null,
      },
      platform: Platform.OS,
    };
  } catch (err) {
    return { error: `Device stats failed: ${(err as Error).message}` };
  }
}

// ─── Contacts ─────────────────────────────────────────────────────────────────

interface ReadContactsArgs {
  query?: string;
  limit?: number;
}

export async function readContacts(args: Record<string, unknown>): Promise<object> {
  const { query, limit = 20 } = args as ReadContactsArgs;

  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const Contacts = require('expo-contacts');

    const { status } = await Contacts.requestPermissionsAsync();
    if (status !== 'granted') {
      return { error: 'Contacts permission denied.' };
    }

    const { data } = await Contacts.getContactsAsync({
      fields: [
        Contacts.Fields.Name,
        Contacts.Fields.PhoneNumbers,
        Contacts.Fields.Emails,
      ],
      name: query,
      pageSize: limit,
    });

    return {
      count: data.length,
      contacts: data.map((c: {
        name: string;
        phoneNumbers?: Array<{ number: string; label: string }>;
        emails?: Array<{ email: string; label: string }>;
      }) => ({
        name: c.name,
        phones: c.phoneNumbers?.map(p => ({ number: p.number, type: p.label })) ?? [],
        emails: c.emails?.map(e => ({ email: e.email, type: e.label })) ?? [],
      })),
    };
  } catch (err) {
    return { error: `Contacts failed: ${(err as Error).message}` };
  }
}
