/**
 * tts.ts — Text-to-Speech tool
 * Atlas sends text, phone speaks it aloud.
 *
 * Package: expo-speech
 */

import * as Speech from 'expo-speech';

interface SpeakArgs {
  text: string;
  rate?: number;   // 0.1 – 2.0, default 0.95
  pitch?: number;  // 0.5 – 2.0, default 1.0
  language?: string; // e.g. 'en-NG', 'en-US'
}

export async function speak(args: Record<string, unknown>): Promise<string> {
  const { text, rate = 0.95, pitch = 1.0, language = 'en-US' } = args as SpeakArgs;

  if (!text) return 'Error: no text provided.';

  // Stop anything currently speaking
  if (await Speech.isSpeakingAsync()) {
    await Speech.stop();
  }

  return new Promise((resolve) => {
    Speech.speak(text, {
      rate: rate as number,
      pitch: pitch as number,
      language: language as string,
      onDone: () => resolve(`Spoke: "${text.slice(0, 60)}${text.length > 60 ? '...' : ''}"`),
      onError: (err) => resolve(`TTS error: ${err.message}`),
    });
  });
}

export async function stopSpeaking(_args: Record<string, unknown>): Promise<string> {
  if (await Speech.isSpeakingAsync()) {
    await Speech.stop();
    return 'Speech stopped.';
  }
  return 'Nothing was playing.';
}
