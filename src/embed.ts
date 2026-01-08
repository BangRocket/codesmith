/**
 * Discord embed for Claude Code status display.
 */

import {
  EmbedBuilder,
  type Message,
  type TextChannel,
  Colors,
} from "discord.js";
import { EMBED_UPDATE_INTERVAL_MS } from "./config.js";
import type { StatusData, UserSession } from "./types.js";

/**
 * Create a text-based progress bar.
 */
function createProgressBar(percent: number, length = 10): string {
  const filled = Math.floor((percent / 100) * length);
  const empty = length - filled;
  return "\u2588".repeat(filled) + "\u2591".repeat(empty);
}

/**
 * Format duration in human-readable form.
 */
function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) {
    return `${seconds}s`;
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${secs}s`;
  } else {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }
}

/**
 * Status embed for a Claude Code session.
 */
export class StatusEmbed {
  private userId: string;
  private username: string;
  private status: StatusData;
  private sessionStart: Date;
  private lastUpdate: Date;
  private message: Message | null = null;

  constructor(userId: string, username: string, initialStatus: StatusData) {
    this.userId = userId;
    this.username = username;
    this.status = initialStatus;
    this.sessionStart = new Date();
    this.lastUpdate = new Date();
  }

  /**
   * Update status data.
   */
  updateStatus(status: StatusData): void {
    this.status = status;
    this.lastUpdate = new Date();
  }

  /**
   * Build the Discord embed.
   */
  buildEmbed(): EmbedBuilder {
    const uptimeMs = Date.now() - this.sessionStart.getTime();
    const uptimeStr = formatDuration(uptimeMs);

    const progress = createProgressBar(this.status.contextPercent);

    // Determine status color based on context usage
    let color: number;
    if (this.status.contextPercent > 90) {
      color = Colors.Red; // Critical
    } else if (this.status.contextPercent > 70) {
      color = Colors.Yellow; // Warning
    } else {
      color = Colors.Green; // Good
    }

    return new EmbedBuilder()
      .setTitle("Claude Code Status")
      .setDescription(`Session for ${this.username}`)
      .setColor(color)
      .addFields(
        { name: "Model", value: this.status.model, inline: true },
        {
          name: "Input Tokens",
          value: this.status.inputTokens.toLocaleString(),
          inline: true,
        },
        {
          name: "Cost",
          value: `$${this.status.cost.toFixed(4)}`,
          inline: true,
        },
        {
          name: "Context Usage",
          value: `${progress} ${this.status.contextPercent.toFixed(0)}%`,
          inline: false,
        },
        { name: "Session", value: `Active (${uptimeStr})`, inline: true }
      )
      .setFooter({ text: "Last updated" })
      .setTimestamp(this.lastUpdate);
  }

  /**
   * Create and optionally pin the status embed in a channel.
   */
  async createMessage(channel: TextChannel): Promise<Message> {
    const embed = this.buildEmbed();
    this.message = await channel.send({ embeds: [embed] });

    // Try to pin the message
    try {
      await this.message.pin();
    } catch {
      // No permission to pin
    }

    return this.message;
  }

  /**
   * Update the existing status message.
   */
  async updateMessage(): Promise<void> {
    if (!this.message) return;

    try {
      const embed = this.buildEmbed();
      await this.message.edit({ embeds: [embed] });
    } catch (error) {
      // Message might be deleted
      this.message = null;
    }
  }

  /**
   * Delete the status message.
   */
  async deleteMessage(): Promise<void> {
    if (!this.message) return;

    try {
      await this.message.unpin();
    } catch {
      // Ignore unpin errors
    }

    try {
      await this.message.delete();
    } catch {
      // Ignore delete errors
    }

    this.message = null;
  }

  /**
   * Get the message object.
   */
  getMessage(): Message | null {
    return this.message;
  }
}

/**
 * Manages status embeds for all users.
 */
export class EmbedManager {
  private embeds: Map<string, StatusEmbed> = new Map();
  private updateInterval: NodeJS.Timeout | null = null;

  /**
   * Start periodic embed updates.
   */
  start(): void {
    this.updateInterval = setInterval(() => {
      this.updateAllEmbeds();
    }, EMBED_UPDATE_INTERVAL_MS);
  }

  /**
   * Stop periodic updates.
   */
  stop(): void {
    if (this.updateInterval) {
      clearInterval(this.updateInterval);
      this.updateInterval = null;
    }
  }

  /**
   * Update all embeds.
   */
  private async updateAllEmbeds(): Promise<void> {
    for (const embed of this.embeds.values()) {
      try {
        await embed.updateMessage();
      } catch (error) {
        console.error("Failed to update embed:", error);
      }
    }
  }

  /**
   * Get or create a status embed for a user.
   */
  getOrCreate(session: UserSession): StatusEmbed {
    let embed = this.embeds.get(session.userId);
    if (!embed) {
      embed = new StatusEmbed(session.userId, session.username, session.status);
      this.embeds.set(session.userId, embed);
    }
    return embed;
  }

  /**
   * Get a user's status embed if it exists.
   */
  get(userId: string): StatusEmbed | undefined {
    return this.embeds.get(userId);
  }

  /**
   * Update status for a user's embed.
   */
  updateStatus(userId: string, status: StatusData): void {
    const embed = this.embeds.get(userId);
    if (embed) {
      embed.updateStatus(status);
    }
  }

  /**
   * Remove and clean up a user's status embed.
   */
  async remove(userId: string): Promise<void> {
    const embed = this.embeds.get(userId);
    if (embed) {
      await embed.deleteMessage();
      this.embeds.delete(userId);
    }
  }
}
