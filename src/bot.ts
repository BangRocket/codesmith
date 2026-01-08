/**
 * Codesmith Discord bot - Claude Code to Discord bridge.
 */

import {
  Client,
  GatewayIntentBits,
  SlashCommandBuilder,
  REST,
  Routes,
  AttachmentBuilder,
  type ChatInputCommandInteraction,
  type Message,
  type TextChannel,
  ChannelType,
} from "discord.js";
import { existsSync, statSync, readFileSync } from "fs";
import { join, basename, resolve } from "path";
import {
  DISCORD_BOT_TOKEN,
  DISCORD_APP_ID,
  CODESMITH_CHANNEL_ID,
  isConfigured,
  getUserWorkspace,
} from "./config.js";
import {
  deleteCredentials,
  getAuthMethod,
  hasValidCredentials,
  storeCredentials,
  validateCredentialsJson,
} from "./auth.js";
import { AuthMethod } from "./types.js";
import { getSessionManager } from "./session.js";
import { EmbedManager } from "./embed.js";
import {
  gitInit,
  gitSetRemote,
  gitPush,
  gitCreate,
  getGitHubFileUrl,
  isGitRepo,
  getRemoteUrl,
} from "./git.js";

/**
 * Codesmith Discord bot.
 */
export class CodesmithBot {
  private client: Client;
  private embedManager: EmbedManager;
  private pendingLogins: Set<string> = new Set();

  constructor() {
    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
        GatewayIntentBits.DirectMessages,
      ],
    });

    this.embedManager = new EmbedManager();
    this.setupEventHandlers();
  }

  /**
   * Set up Discord event handlers.
   */
  private setupEventHandlers(): void {
    this.client.on("ready", () => {
      console.log(`Logged in as ${this.client.user?.tag}`);
      console.log(`Connected to ${this.client.guilds.cache.size} guild(s)`);
    });

    this.client.on("interactionCreate", async (interaction) => {
      if (!interaction.isChatInputCommand()) return;
      await this.handleCommand(interaction);
    });

    this.client.on("messageCreate", async (message) => {
      await this.handleMessage(message);
    });
  }

  /**
   * Handle slash commands.
   */
  private async handleCommand(interaction: ChatInputCommandInteraction): Promise<void> {
    const { commandName, options } = interaction;

    // All commands are under /cc group
    if (commandName !== "cc") return;

    const subcommand = options.getSubcommand();
    const userId = interaction.user.id;

    try {
      switch (subcommand) {
        case "start":
          await this.handleStart(interaction);
          break;
        case "stop":
          await this.handleStop(interaction);
          break;
        case "clear":
          await this.handleSlashCommand(interaction, "/clear");
          break;
        case "compact":
          await this.handleSlashCommand(interaction, "/compact");
          break;
        case "model":
          const model = options.getString("model", true);
          await this.handleSlashCommand(interaction, `/model ${model}`);
          break;
        case "status":
          await this.handleStatus(interaction);
          break;
        case "login":
          await this.handleLogin(interaction);
          break;
        case "logout":
          await this.handleLogout(interaction);
          break;
        case "download":
          await this.handleDownload(interaction);
          break;
        case "git":
          await this.handleGit(interaction);
          break;
        default:
          await interaction.reply({ content: "Unknown command", ephemeral: true });
      }
    } catch (error) {
      console.error("Command error:", error);
      const errorMessage = error instanceof Error ? error.message : "Unknown error";

      if (interaction.replied || interaction.deferred) {
        await interaction.followUp({ content: `Error: ${errorMessage}`, ephemeral: true });
      } else {
        await interaction.reply({ content: `Error: ${errorMessage}`, ephemeral: true });
      }
    }
  }

  /**
   * Handle /cc start command.
   */
  private async handleStart(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;
    const username = interaction.user.displayName;
    const channel = interaction.channel as TextChannel;

    // Check authentication
    const authMethod = getAuthMethod(userId);
    if (authMethod === AuthMethod.NONE) {
      await interaction.reply({
        content:
          "No authentication configured.\n" +
          "Use `/cc login` to authenticate with your Claude Max/Pro subscription, " +
          "or ask the server admin to set `ANTHROPIC_API_KEY`.",
        ephemeral: true,
      });
      return;
    }

    const authType = authMethod === AuthMethod.OAUTH ? "OAuth" : "API key";
    await interaction.reply({
      content: `Starting Claude Code session (${authType})...`,
      ephemeral: true,
    });

    try {
      const sessionManager = getSessionManager();
      const session = await sessionManager.startSession(userId, username, channel);

      // Create status embed
      const embed = this.embedManager.getOrCreate(session);
      await embed.createMessage(channel);

      // Set up status update callback
      sessionManager.setStatusCallback(userId, (status) => {
        this.embedManager.updateStatus(userId, status);
      });

      await interaction.followUp({
        content:
          "Session started! Messages in this channel go to Claude Code.\n" +
          "Use `/cc stop` to end the session.",
        ephemeral: true,
      });
    } catch (error) {
      console.error("Failed to start session:", error);
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      await interaction.followUp({
        content: `Failed to start session: ${errorMessage}`,
        ephemeral: true,
      });
    }
  }

  /**
   * Handle /cc stop command.
   */
  private async handleStop(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;
    const sessionManager = getSessionManager();

    if (!sessionManager.hasSession(userId)) {
      await interaction.reply({
        content: "You don't have an active session.",
        ephemeral: true,
      });
      return;
    }

    await sessionManager.stopSession(userId);
    await this.embedManager.remove(userId);

    await interaction.reply({ content: "Session stopped.", ephemeral: true });
  }

  /**
   * Handle slash commands that are passed to Claude Code.
   */
  private async handleSlashCommand(
    interaction: ChatInputCommandInteraction,
    command: string
  ): Promise<void> {
    const userId = interaction.user.id;
    const sessionManager = getSessionManager();

    if (await sessionManager.sendSlashCommand(userId, command)) {
      await interaction.reply({
        content: `Sent ${command} to Claude Code`,
        ephemeral: true,
      });
    } else {
      await interaction.reply({ content: "No active session", ephemeral: true });
    }
  }

  /**
   * Handle /cc status command.
   */
  private async handleStatus(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;
    const sessionManager = getSessionManager();
    const info = sessionManager.getSessionInfo(userId);

    if (!info) {
      await interaction.reply({ content: "No active session", ephemeral: true });
      return;
    }

    const uptime = info.uptimeSeconds as number;
    const idle = info.idleSeconds as number;

    await interaction.reply({
      content:
        `**Session Status**\n` +
        `Uptime: ${Math.floor(uptime / 60)}m ${uptime % 60}s\n` +
        `Idle: ${idle}s\n` +
        `Status: ${info.isActive ? "Active" : "Stopped"}`,
      ephemeral: true,
    });
  }

  /**
   * Handle /cc login command.
   */
  private async handleLogin(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;

    // Check if already authenticated
    if (hasValidCredentials(userId)) {
      await interaction.reply({
        content:
          "You already have OAuth credentials stored. " +
          "Use `/cc logout` first if you want to re-authenticate.",
        ephemeral: true,
      });
      return;
    }

    // Add to pending logins
    this.pendingLogins.add(userId);

    await interaction.reply({
      content: "Check your DMs for authentication instructions.",
      ephemeral: true,
    });

    // DM the user with instructions
    try {
      const dmChannel = await interaction.user.createDM();
      await dmChannel.send(
        "**Claude Max/Pro Authentication**\n\n" +
          "To use your Claude Max/Pro subscription with Codesmith:\n\n" +
          "1. Open a terminal on your computer\n" +
          "2. Run: `claude login`\n" +
          "3. Select **\"Claude account with subscription\"**\n" +
          "4. Complete the login in your browser\n" +
          "5. Run: `cat ~/.claude/.credentials.json`\n" +
          "6. Copy the **entire JSON output** and paste it here\n\n" +
          "Your credentials will be stored securely and used for your sessions."
      );
    } catch {
      this.pendingLogins.delete(userId);
      await interaction.followUp({
        content: "Could not send DM. Please enable DMs from server members.",
        ephemeral: true,
      });
    }
  }

  /**
   * Handle /cc logout command.
   */
  private async handleLogout(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;

    if (deleteCredentials(userId)) {
      await interaction.reply({
        content: "Your Claude credentials have been removed.",
        ephemeral: true,
      });
    } else {
      await interaction.reply({
        content: "No stored credentials to remove.",
        ephemeral: true,
      });
    }
  }

  /**
   * Handle /cc download command - returns GitHub URL to file.
   */
  private async handleDownload(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;
    const filePath = interaction.options.getString("path", true);

    // Get user's workspace
    const workspace = getUserWorkspace(userId);

    // Check if workspace is a git repo with remote
    if (!isGitRepo(workspace)) {
      await interaction.reply({
        content: "No git repository. Run `/cc git init` and `/cc git remote <url>` first.",
        ephemeral: true,
      });
      return;
    }

    const remoteUrl = await getRemoteUrl(workspace);
    if (!remoteUrl) {
      await interaction.reply({
        content: "No git remote configured. Run `/cc git remote <url>` or `/cc git create <name>` first.",
        ephemeral: true,
      });
      return;
    }

    // Resolve the path relative to workspace, preventing path traversal
    const resolvedPath = resolve(workspace, filePath);
    if (!resolvedPath.startsWith(workspace)) {
      await interaction.reply({
        content: "Invalid path: cannot access files outside your workspace.",
        ephemeral: true,
      });
      return;
    }

    // Check if file exists
    if (!existsSync(resolvedPath)) {
      await interaction.reply({
        content: `File not found: \`${filePath}\``,
        ephemeral: true,
      });
      return;
    }

    // Get GitHub URL
    const githubUrl = await getGitHubFileUrl(workspace, filePath);

    if (!githubUrl) {
      await interaction.reply({
        content: "Could not generate GitHub URL. Make sure the remote is a GitHub repository.",
        ephemeral: true,
      });
      return;
    }

    // Remind user to push first
    await interaction.reply({
      content: `**File:** \`${filePath}\`\n**GitHub:** ${githubUrl}\n\n*Note: Make sure you've pushed recent changes with \`/cc git push\`*`,
    });
  }

  /**
   * Handle /cc git commands.
   */
  private async handleGit(interaction: ChatInputCommandInteraction): Promise<void> {
    const userId = interaction.user.id;
    const action = interaction.options.getString("action", true);

    switch (action) {
      case "init": {
        const result = await gitInit(userId);
        await interaction.reply({ content: result.message, ephemeral: !result.success });
        break;
      }

      case "remote": {
        const url = interaction.options.getString("url");
        if (!url) {
          // Show current remote
          const workspace = getUserWorkspace(userId);
          const currentRemote = await getRemoteUrl(workspace);
          await interaction.reply({
            content: currentRemote ? `Current remote: ${currentRemote}` : "No remote configured.",
            ephemeral: true,
          });
        } else {
          const result = await gitSetRemote(userId, url);
          await interaction.reply({ content: result.message, ephemeral: !result.success });
        }
        break;
      }

      case "push": {
        await interaction.deferReply();
        const message = interaction.options.getString("message") ?? undefined;
        const result = await gitPush(userId, message);
        await interaction.editReply({
          content: result.success && result.url
            ? `${result.message}\n${result.url}`
            : result.message,
        });
        break;
      }

      case "create": {
        const name = interaction.options.getString("name");
        if (!name) {
          await interaction.reply({
            content: "Please provide a repository name.",
            ephemeral: true,
          });
          return;
        }

        await interaction.deferReply();
        const result = await gitCreate(userId, name);
        await interaction.editReply({
          content: result.success && result.url
            ? `${result.message}\n${result.url}`
            : result.message,
        });
        break;
      }

      default:
        await interaction.reply({
          content: "Unknown git action. Use: init, remote, push, or create.",
          ephemeral: true,
        });
    }
  }

  /**
   * Handle incoming messages.
   */
  private async handleMessage(message: Message): Promise<void> {
    // Ignore bot messages
    if (message.author.bot) return;

    const userId = message.author.id;

    // Handle DMs for credential paste
    if (message.channel.type === ChannelType.DM) {
      if (this.pendingLogins.has(userId)) {
        await this.handleCredentialPaste(message);
      }
      return;
    }

    // Check if we should process this channel
    if (CODESMITH_CHANNEL_ID && message.channel.id !== CODESMITH_CHANNEL_ID) {
      return;
    }

    // Check if user has an active session
    const sessionManager = getSessionManager();
    if (!sessionManager.hasSession(userId)) {
      return; // No session, ignore regular messages
    }

    // Send message content to Claude Code
    const success = await sessionManager.sendInput(userId, message.content);
    if (success) {
      // Add reaction to indicate message was sent
      try {
        await message.react("\uD83D\uDCE8"); // envelope emoji
      } catch {
        // No permission to react
      }
    }
  }

  /**
   * Handle credential JSON pasted in DM.
   */
  private async handleCredentialPaste(message: Message): Promise<void> {
    const userId = message.author.id;
    const content = message.content.trim();
    const channel = message.channel;

    // Type guard - we only call this for DMs which have send()
    if (!("send" in channel)) return;

    // Validate the JSON
    const credentials = validateCredentialsJson(content);

    if (!credentials) {
      await channel.send(
        "Invalid credentials format. Please paste the entire contents of " +
          "`~/.claude/.credentials.json` (should be valid JSON with " +
          "`claudeAiOauth` key)."
      );
      return;
    }

    // Store credentials
    try {
      storeCredentials(userId, credentials);
      this.pendingLogins.delete(userId);

      await channel.send(
        "Credentials saved successfully! You can now use `/cc start` " +
          "to begin a Claude Code session with your Max/Pro subscription."
      );
      console.log(`Stored OAuth credentials for user ${userId}`);
    } catch (error) {
      console.error(`Failed to store credentials for ${userId}:`, error);
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      await channel.send(`Failed to save credentials: ${errorMessage}`);
    }
  }

  /**
   * Register slash commands with Discord.
   */
  async registerCommands(): Promise<void> {
    const commands = [
      new SlashCommandBuilder()
        .setName("cc")
        .setDescription("Claude Code commands")
        .addSubcommand((sub) =>
          sub.setName("start").setDescription("Start a new Claude Code session")
        )
        .addSubcommand((sub) =>
          sub.setName("stop").setDescription("Stop your Claude Code session")
        )
        .addSubcommand((sub) =>
          sub.setName("clear").setDescription("Clear Claude Code conversation history")
        )
        .addSubcommand((sub) =>
          sub.setName("compact").setDescription("Compact Claude Code conversation")
        )
        .addSubcommand((sub) =>
          sub
            .setName("model")
            .setDescription("Change Claude Code model")
            .addStringOption((opt) =>
              opt
                .setName("model")
                .setDescription("Model name (e.g., sonnet, opus, haiku)")
                .setRequired(true)
            )
        )
        .addSubcommand((sub) =>
          sub.setName("status").setDescription("Show session status")
        )
        .addSubcommand((sub) =>
          sub
            .setName("login")
            .setDescription("Authenticate with your Claude Max/Pro subscription")
        )
        .addSubcommand((sub) =>
          sub.setName("logout").setDescription("Remove your stored Claude credentials")
        )
        .addSubcommand((sub) =>
          sub
            .setName("download")
            .setDescription("Get GitHub link to a file in your workspace")
            .addStringOption((opt) =>
              opt
                .setName("path")
                .setDescription("Path to file (relative to your workspace)")
                .setRequired(true)
            )
        )
        .addSubcommand((sub) =>
          sub
            .setName("git")
            .setDescription("Git operations for your workspace")
            .addStringOption((opt) =>
              opt
                .setName("action")
                .setDescription("Git action to perform")
                .setRequired(true)
                .addChoices(
                  { name: "init", value: "init" },
                  { name: "remote", value: "remote" },
                  { name: "push", value: "push" },
                  { name: "create", value: "create" }
                )
            )
            .addStringOption((opt) =>
              opt
                .setName("url")
                .setDescription("Remote URL (for 'remote' action)")
                .setRequired(false)
            )
            .addStringOption((opt) =>
              opt
                .setName("name")
                .setDescription("Repository name (for 'create' action)")
                .setRequired(false)
            )
            .addStringOption((opt) =>
              opt
                .setName("message")
                .setDescription("Commit message (for 'push' action)")
                .setRequired(false)
            )
        ),
    ];

    const rest = new REST().setToken(DISCORD_BOT_TOKEN);

    try {
      console.log("Registering slash commands...");
      await rest.put(Routes.applicationCommands(DISCORD_APP_ID), {
        body: commands.map((cmd) => cmd.toJSON()),
      });
      console.log("Slash commands registered successfully");
    } catch (error) {
      console.error("Failed to register slash commands:", error);
      throw error;
    }
  }

  /**
   * Start the bot.
   */
  async start(): Promise<void> {
    if (!isConfigured()) {
      throw new Error("Missing required configuration. Set DISCORD_BOT_TOKEN.");
    }

    // Register commands
    await this.registerCommands();

    // Start session manager
    const sessionManager = getSessionManager();
    sessionManager.start();

    // Start embed update loop
    this.embedManager.start();

    // Connect to Discord
    await this.client.login(DISCORD_BOT_TOKEN);
  }

  /**
   * Stop the bot.
   */
  async stop(): Promise<void> {
    // Stop embed updates
    this.embedManager.stop();

    // Stop all sessions
    const sessionManager = getSessionManager();
    await sessionManager.stop();

    // Disconnect from Discord
    this.client.destroy();
  }
}
