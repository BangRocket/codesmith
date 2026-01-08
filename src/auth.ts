/**
 * Authentication handling for Claude Code sessions.
 */

import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "fs";
import { dirname } from "path";
import { ANTHROPIC_API_KEY, getCredentialsPath, getUserWorkspace } from "./config.js";
import { AuthMethod, type OAuthCredentials } from "./types.js";

/**
 * Validate and parse credentials JSON.
 */
export function validateCredentialsJson(jsonStr: string): OAuthCredentials | null {
  try {
    const data = JSON.parse(jsonStr.trim());

    if (!data.claudeAiOauth) {
      return null;
    }

    const oauth = data.claudeAiOauth;
    const requiredKeys = ["accessToken", "refreshToken", "expiresAt"];

    for (const key of requiredKeys) {
      if (!(key in oauth)) {
        return null;
      }
    }

    if (typeof oauth.accessToken !== "string" || !oauth.accessToken) {
      return null;
    }
    if (typeof oauth.refreshToken !== "string" || !oauth.refreshToken) {
      return null;
    }
    if (typeof oauth.expiresAt !== "number") {
      return null;
    }

    return data as OAuthCredentials;
  } catch {
    return null;
  }
}

/**
 * Store credentials in user's workspace.
 */
export function storeCredentials(userId: string, credentials: OAuthCredentials): string {
  const credsPath = getCredentialsPath(userId);
  const credsDir = dirname(credsPath);

  // Ensure directory exists
  if (!existsSync(credsDir)) {
    mkdirSync(credsDir, { recursive: true });
  }

  // Write credentials
  writeFileSync(credsPath, JSON.stringify(credentials, null, 2), {
    mode: 0o600,
  });

  console.log(`Stored credentials for user ${userId}`);
  return credsPath;
}

/**
 * Check if user has stored OAuth credentials.
 */
export function hasValidCredentials(userId: string): boolean {
  const credsPath = getCredentialsPath(userId);

  if (!existsSync(credsPath)) {
    return false;
  }

  try {
    const data = readFileSync(credsPath, "utf-8");
    return validateCredentialsJson(data) !== null;
  } catch {
    return false;
  }
}

/**
 * Get the credentials path if user has valid OAuth credentials.
 */
export function getCredentialsPathIfValid(userId: string): string | null {
  if (hasValidCredentials(userId)) {
    return getCredentialsPath(userId);
  }
  return null;
}

/**
 * Determine authentication method for a user.
 *
 * Priority:
 * 1. Per-user OAuth credentials
 * 2. Global ANTHROPIC_API_KEY
 * 3. None
 */
export function getAuthMethod(userId: string): AuthMethod {
  if (hasValidCredentials(userId)) {
    return AuthMethod.OAUTH;
  }

  if (ANTHROPIC_API_KEY) {
    return AuthMethod.API_KEY;
  }

  return AuthMethod.NONE;
}

/**
 * Remove user's stored credentials.
 */
export function deleteCredentials(userId: string): boolean {
  const credsPath = getCredentialsPath(userId);

  if (existsSync(credsPath)) {
    unlinkSync(credsPath);
    console.log(`Deleted credentials for user ${userId}`);
    return true;
  }

  return false;
}

/**
 * Get environment variables for Claude Code based on auth method.
 */
export function getAuthEnv(userId: string): Record<string, string> {
  const authMethod = getAuthMethod(userId);
  const env: Record<string, string> = {};

  if (authMethod === AuthMethod.API_KEY && ANTHROPIC_API_KEY) {
    env.ANTHROPIC_API_KEY = ANTHROPIC_API_KEY;
  }

  // For OAuth, Claude Code will read from ~/.claude/.credentials.json
  // We need to set CLAUDE_CONFIG_DIR to point to the user's .claude directory
  if (authMethod === AuthMethod.OAUTH) {
    const workspace = getUserWorkspace(userId);
    env.CLAUDE_CONFIG_DIR = `${workspace}/.claude`;
  }

  return env;
}
