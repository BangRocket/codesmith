/**
 * Session manager for Claude Code user sessions.
 */

import type { TextChannel } from "discord.js";
import { SESSION_TIMEOUT_MS } from "./config.js";
import { defaultStatus, runClaudeQuery, sendSlashCommand } from "./claude.js";
import type { StatusData, UserSession } from "./types.js";

/**
 * Manages all user Claude Code sessions.
 */
export class SessionManager {
  private sessions: Map<string, UserSession> = new Map();
  private cleanupInterval: NodeJS.Timeout | null = null;
  private statusUpdateCallbacks: Map<string, (status: StatusData) => void> = new Map();

  /**
   * Start the session manager background tasks.
   */
  start(): void {
    // Cleanup expired sessions every minute
    this.cleanupInterval = setInterval(() => {
      this.cleanupExpiredSessions();
    }, 60_000);
  }

  /**
   * Stop the session manager and clean up all sessions.
   */
  async stop(): Promise<void> {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }

    // Stop all sessions
    const userIds = Array.from(this.sessions.keys());
    for (const userId of userIds) {
      await this.stopSession(userId);
    }
  }

  /**
   * Clean up expired sessions.
   */
  private cleanupExpiredSessions(): void {
    const now = Date.now();

    for (const [userId, session] of this.sessions) {
      const idleMs = now - session.lastActivity.getTime();

      if (idleMs > SESSION_TIMEOUT_MS || !session.isActive) {
        console.log(`Cleaning up expired session for user ${userId}`);
        this.stopSession(userId);
      }
    }
  }

  /**
   * Check if user has an active session.
   */
  hasSession(userId: string): boolean {
    const session = this.sessions.get(userId);
    return session !== undefined && session.isActive;
  }

  /**
   * Get a user's session if it exists.
   */
  getSession(userId: string): UserSession | undefined {
    const session = this.sessions.get(userId);
    if (session && session.isActive) {
      return session;
    }
    return undefined;
  }

  /**
   * Set callback for status updates.
   */
  setStatusCallback(userId: string, callback: (status: StatusData) => void): void {
    this.statusUpdateCallbacks.set(userId, callback);
  }

  /**
   * Start a new Claude Code session for a user.
   */
  async startSession(
    userId: string,
    username: string,
    channel: TextChannel
  ): Promise<UserSession> {
    // Stop existing session if any
    if (this.sessions.has(userId)) {
      await this.stopSession(userId);
    }

    const session: UserSession = {
      userId,
      username,
      channel,
      abortController: new AbortController(),
      createdAt: new Date(),
      lastActivity: new Date(),
      status: defaultStatus(),
      isActive: true,
    };

    this.sessions.set(userId, session);
    console.log(`Started session for user ${userId}`);

    return session;
  }

  /**
   * Stop a user's session.
   */
  async stopSession(userId: string): Promise<void> {
    const session = this.sessions.get(userId);

    if (session) {
      // Abort any running query
      session.abortController.abort();
      session.isActive = false;

      this.sessions.delete(userId);
      this.statusUpdateCallbacks.delete(userId);

      console.log(`Stopped session for user ${userId}`);
    }
  }

  /**
   * Send input to a user's session.
   */
  async sendInput(userId: string, text: string): Promise<boolean> {
    const session = this.getSession(userId);
    if (!session) {
      return false;
    }

    const callback = this.statusUpdateCallbacks.get(userId) || (() => {});

    // Run the query
    await runClaudeQuery(session, text, callback);
    return true;
  }

  /**
   * Send a slash command to a user's session.
   */
  async sendSlashCommand(userId: string, command: string): Promise<boolean> {
    const session = this.getSession(userId);
    if (!session) {
      return false;
    }

    const callback = this.statusUpdateCallbacks.get(userId) || (() => {});

    await sendSlashCommand(session, command, callback);
    return true;
  }

  /**
   * Get info about a user's session.
   */
  getSessionInfo(userId: string): Record<string, unknown> | null {
    const session = this.getSession(userId);
    if (!session) {
      return null;
    }

    const now = Date.now();
    const uptimeMs = now - session.createdAt.getTime();
    const idleMs = now - session.lastActivity.getTime();

    return {
      userId,
      createdAt: session.createdAt.toISOString(),
      uptimeSeconds: Math.floor(uptimeMs / 1000),
      idleSeconds: Math.floor(idleMs / 1000),
      status: session.status,
      isActive: session.isActive,
    };
  }

  /**
   * Get all active sessions.
   */
  getAllSessions(): Map<string, UserSession> {
    const active = new Map<string, UserSession>();
    for (const [userId, session] of this.sessions) {
      if (session.isActive) {
        active.set(userId, session);
      }
    }
    return active;
  }
}

// Singleton instance
let sessionManager: SessionManager | null = null;

/**
 * Get the global session manager instance.
 */
export function getSessionManager(): SessionManager {
  if (!sessionManager) {
    sessionManager = new SessionManager();
  }
  return sessionManager;
}
