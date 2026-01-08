/**
 * Session manager for Claude Code user sessions.
 */

import type { TextChannel } from "discord.js";
import { SESSION_TIMEOUT_MS } from "./config.js";
import { defaultStatus, runClaudeQuery, sendSlashCommand } from "./claude.js";
import type { StatusData, UserSession } from "./types.js";
import { sessionLogger as log } from "./logger.js";

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
    log.info("Session manager started");
    // Cleanup expired sessions every minute
    this.cleanupInterval = setInterval(() => {
      this.cleanupExpiredSessions();
    }, 60_000);
  }

  /**
   * Stop the session manager and clean up all sessions.
   */
  async stop(): Promise<void> {
    log.info("Session manager stopping");
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }

    // Stop all sessions
    const userIds = Array.from(this.sessions.keys());
    for (const userId of userIds) {
      await this.stopSession(userId);
    }
    log.info("Session manager stopped");
  }

  /**
   * Clean up expired sessions.
   */
  private cleanupExpiredSessions(): void {
    const now = Date.now();
    log.debug(`Running cleanup check, ${this.sessions.size} sessions active`);

    for (const [userId, session] of this.sessions) {
      const idleMs = now - session.lastActivity.getTime();
      const idleSec = Math.floor(idleMs / 1000);
      const timeoutSec = Math.floor(SESSION_TIMEOUT_MS / 1000);

      log.debug(`Session ${userId}: idle=${idleSec}s, timeout=${timeoutSec}s, isActive=${session.isActive}`);

      if (idleMs > SESSION_TIMEOUT_MS) {
        log.info(`Session ${userId} expired (idle ${idleSec}s > timeout ${timeoutSec}s)`);
        this.stopSession(userId);
      } else if (!session.isActive) {
        log.info(`Session ${userId} marked inactive, cleaning up`);
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
    log.info(`Starting session for user ${userId} (${username})`);

    // Stop existing session if any
    if (this.sessions.has(userId)) {
      log.info(`User ${userId} has existing session, stopping it first`);
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
    log.info(`Session started for user ${userId}`, {
      channelId: channel.id,
      createdAt: session.createdAt.toISOString(),
    });

    return session;
  }

  /**
   * Stop a user's session.
   */
  async stopSession(userId: string): Promise<void> {
    const session = this.sessions.get(userId);

    if (session) {
      log.info(`Stopping session for user ${userId}`, {
        wasActive: session.isActive,
        uptime: Math.floor((Date.now() - session.createdAt.getTime()) / 1000),
      });

      // Abort any running query
      session.abortController.abort();
      session.isActive = false;

      this.sessions.delete(userId);
      this.statusUpdateCallbacks.delete(userId);

      log.info(`Session stopped for user ${userId}`);
    } else {
      log.warn(`Attempted to stop non-existent session for user ${userId}`);
    }
  }

  /**
   * Send input to a user's session.
   */
  async sendInput(userId: string, text: string): Promise<boolean> {
    const session = this.getSession(userId);
    if (!session) {
      log.warn(`sendInput called for user ${userId} but no active session`);
      return false;
    }

    log.info(`Sending input to session ${userId}`, {
      textLength: text.length,
      textPreview: text.substring(0, 100),
    });

    const callback = this.statusUpdateCallbacks.get(userId) || (() => {});

    try {
      // Run the query
      await runClaudeQuery(session, text, callback);
      log.info(`Query completed for user ${userId}`);
    } catch (error) {
      log.error(`Query failed for user ${userId}`, {
        error: error instanceof Error ? error.message : String(error),
        stack: error instanceof Error ? error.stack : undefined,
      });
    }

    return true;
  }

  /**
   * Send a slash command to a user's session.
   */
  async sendSlashCommand(userId: string, command: string): Promise<boolean> {
    const session = this.getSession(userId);
    if (!session) {
      log.warn(`sendSlashCommand called for user ${userId} but no active session`);
      return false;
    }

    log.info(`Sending slash command to session ${userId}`, { command });

    const callback = this.statusUpdateCallbacks.get(userId) || (() => {});

    try {
      await sendSlashCommand(session, command, callback);
      log.info(`Slash command completed for user ${userId}`);
    } catch (error) {
      log.error(`Slash command failed for user ${userId}`, {
        error: error instanceof Error ? error.message : String(error),
      });
    }

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
