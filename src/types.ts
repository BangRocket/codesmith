/**
 * Type definitions for Codesmith.
 */

import type { Message, TextChannel } from "discord.js";

/**
 * Authentication method for Claude Code.
 */
export enum AuthMethod {
  OAUTH = "oauth",
  API_KEY = "api_key",
  NONE = "none",
}

/**
 * Parsed status data from Claude Code messages.
 */
export interface StatusData {
  model: string;
  inputTokens: number;
  outputTokens: number;
  cost: number;
  contextPercent: number;
}

/**
 * User session state.
 */
export interface UserSession {
  userId: string;
  username: string;
  channel: TextChannel;
  abortController: AbortController;
  sessionId?: string;
  createdAt: Date;
  lastActivity: Date;
  status: StatusData;
  isActive: boolean;
  statusMessage?: Message;
}

/**
 * Claude Code SDK message types we care about.
 */
export interface ClaudeMessage {
  type: string;
  content?: string;
  tool?: string;
  result?: string;
  error?: string;
}

/**
 * Stored OAuth credentials structure.
 */
export interface OAuthCredentials {
  claudeAiOauth: {
    accessToken: string;
    refreshToken: string;
    expiresAt: number;
  };
}
