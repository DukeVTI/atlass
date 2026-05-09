/**
 * location.ts — GPS / Location tools
 * Atlas can request current location or start continuous tracking.
 *
 * Package: expo-location
 */

import * as Location from 'expo-location';

export async function getLocation(_args: Record<string, unknown>): Promise<object> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  if (status !== 'granted') {
    return { error: 'Location permission denied.' };
  }

  try {
    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    });

    // Reverse geocode to get human-readable address
    let address = null;
    try {
      const [place] = await Location.reverseGeocodeAsync({
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
      });
      if (place) {
        address = [
          place.name,
          place.street,
          place.district,
          place.city,
          place.region,
          place.country,
        ].filter(Boolean).join(', ');
      }
    } catch { /* address is optional */ }

    return {
      latitude: loc.coords.latitude,
      longitude: loc.coords.longitude,
      accuracy_meters: Math.round(loc.coords.accuracy ?? 0),
      altitude_meters: loc.coords.altitude ? Math.round(loc.coords.altitude) : null,
      speed_kmh: loc.coords.speed ? Math.round(loc.coords.speed * 3.6) : null,
      address,
      timestamp: new Date(loc.timestamp).toISOString(),
    };
  } catch (err) {
    return { error: `Location failed: ${(err as Error).message}` };
  }
}
