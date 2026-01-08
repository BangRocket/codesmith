/**
 * Configuration module for Codesmith.
 */

import { config } from "dotenv";
import { existsSync, mkdirSync } from "fs";
import { join } from "path";

config();

// Discord Configuration
export const DISCORD_BOT_TOKEN = process.env.DISCORD_BOT_TOKEN ?? "";
export const DISCORD_APP_ID = process.env.DISCORD_APP_ID ?? "";
export const CODESMITH_CHANNEL_ID = process.env.CODESMITH_CHANNEL_ID ?? "";

// Anthropic API Key (optional - passed to Claude Code if set)
// If not set, users can authenticate with their own Max/Pro subscription
export const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY ?? undefined;

// Workspace Configuration
const workspaceDefault = process.env.HOME
  ? join(process.env.HOME, ".codesmith", "workspaces")
  : "/var/codesmith/workspaces";
export const WORKSPACE_BASE = process.env.CODESMITH_WORKSPACE_BASE ?? workspaceDefault;

// Session Configuration
export const SESSION_TIMEOUT_MS = parseInt(
  process.env.CODESMITH_SESSION_TIMEOUT ?? "3600000",
  10
); // 1 hour default

// Discord Limits
export const DISCORD_MSG_LIMIT = 2000;
export const EMBED_UPDATE_INTERVAL_MS = 3000;

// Claude Code Configuration
export const DEFAULT_MODEL = process.env.CODESMITH_DEFAULT_MODEL ?? "sonnet";

/**
 * Check if required configuration is present.
 */
export function isConfigured(): boolean {
  return Boolean(DISCORD_BOT_TOKEN);
}

/**
 * Get the workspace directory for a specific user.
 */
export function getUserWorkspace(userId: string): string {
  // Sanitize userId for filesystem safety
  const safeId = userId.replace(/[^a-zA-Z0-9_-]/g, "");
  return join(WORKSPACE_BASE, safeId);
}

/**
 * Ensure user workspace directory exists and return path.
 */
export function ensureWorkspace(userId: string): string {
  const workspace = getUserWorkspace(userId);
  if (!existsSync(workspace)) {
    mkdirSync(workspace, { recursive: true });
  }
  return workspace;
}

/**
 * Get the credentials path for a user.
 */
export function getCredentialsPath(userId: string): string {
  const workspace = getUserWorkspace(userId);
  return join(workspace, ".claude", ".credentials.json");
}
