import discord
from discord.ext import commands
import json
import os
from datetime import datetime

# Constants
INVITE_DATA_JSON = "invite_data.json"
INVITES_JSON = "invites.json"
CONFIG_JSON = "config.json"


class Logger:
    @staticmethod
    def log(message: str) -> None:
        """Log a message with a timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")


class FileManager:
    @staticmethod
    def read_json_file(file_path: str):
        """Read a JSON file and return its content."""
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def write_json_file(file_path: str, data) -> None:
        """Write data to a JSON file."""
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            Logger.log(f"{file_path} updated successfully.")
        except Exception as e:
            Logger.log(f"Error saving {file_path}: {str(e)}")


# Load configuration
config = FileManager.read_json_file(CONFIG_JSON)
bot_token = config.get("bot_token")
command_prefix = config.get("command_prefix", "!")
intents = discord.Intents.default()
intents.members = config.get("intents", {}).get("members", False)
intents.invites = config.get("intents", {}).get("invites", False)
intents.message_content = config.get("intents", {}).get("message_content", False)


class InviteManager:
    def __init__(self):
        self.invite_data = FileManager.read_json_file(INVITE_DATA_JSON)
        self.invites = FileManager.read_json_file(INVITES_JSON)
        self.guild_invite_caches = {}

    async def update_user_display_name(self, user_id: str, guild):
        """Update the display name for a user in invite_data.json"""
        try:
            # Get the member from the guild
            member = guild.get_member(int(user_id))
            if member:
                # Update display_name in invite_data
                if user_id in self.invite_data:
                    old_display_name = self.invite_data[user_id].get(
                        "display_name", "N/A"
                    )
                    new_display_name = member.display_name

                    self.invite_data[user_id]["display_name"] = new_display_name

                    if old_display_name != new_display_name:
                        Logger.log(
                            f"Updated display name for {user_id}: '{old_display_name}' -> '{new_display_name}'"
                        )
                        return True
                return False
        except Exception as e:
            Logger.log(f"Error updating display name for user {user_id}: {str(e)}")
            return False

    async def ensure_all_display_names(self, guild):
        """Ensure all users in invite_data have display_name fields"""
        updated = False

        for user_id, user_data in self.invite_data.items():
            if "display_name" not in user_data:
                # Try to get display name from guild member
                member = guild.get_member(int(user_id))
                if member:
                    user_data["display_name"] = member.display_name
                    Logger.log(
                        f"Added display name '{member.display_name}' for user {user_id}"
                    )
                    updated = True
                else:
                    # Fallback to username if member not found
                    user_data["display_name"] = user_data.get("username", "Unknown")
                    Logger.log(
                        f"Member {user_id} not found in guild, using username as display name"
                    )
                    updated = True

        if updated:
            FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)

        return updated

    async def validate_invites(self, guild):
        """Validate invites and clean up inactive ones."""
        try:
            current_invites = await guild.invites()
        except discord.Forbidden:
            Logger.log(f"No permission to view invites in {guild.name}")
            return

        for inviter_id, data in self.invite_data.items():
            active_invites = data["active_invites"]
            for invite_code in list(active_invites.keys()):
                if invite_code not in {invite.code for invite in current_invites}:
                    del active_invites[invite_code]
                    Logger.log(
                        f"Removed inactive invite {invite_code} from {data['username']}'s active invites"
                    )

        FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)

    async def initialize_invites(self, guild):
        """Initialize invite tracking for a guild."""
        Logger.log(f"\nInitializing invites for guild: {guild.name} (ID: {guild.id})")
        self.guild_invite_caches[guild.id] = {}
        await self.validate_invites(guild)

        try:
            invites = await guild.invites()
            Logger.log(f"Found {len(invites)} existing invites")
            existing_codes = {invite_entry["code"] for invite_entry in self.invites}

            for invite in invites:
                self.guild_invite_caches[guild.id][invite.code] = invite.uses

                if invite.code not in existing_codes and invite.inviter:
                    invite_entry = {
                        "code": invite.code,
                        "inviter_id": invite.inviter.id,
                        "inviter_display_name": invite.inviter.display_name,
                        "channel_id": invite.channel.id,
                        "created_at": (
                            invite.created_at.isoformat()
                            if invite.created_at
                            else datetime.utcnow().isoformat()
                        ),
                        "max_uses": invite.max_uses,
                        "temporary": invite.temporary,
                        "uses": invite.uses,
                    }
                    self.invites.append(invite_entry)
                    Logger.log(f"Added existing invite {invite.code} to invites.json")

                if not invite.inviter:
                    continue

                inviter_id = str(invite.inviter.id)
                if inviter_id not in self.invite_data:
                    self.invite_data[inviter_id] = {
                        "username": str(invite.inviter),
                        "display_name": invite.inviter.display_name,  # Add display_name here
                        "active_invites": {},
                        "successful_invites": 0,
                        "recruitment_ledger": [],
                    }
                else:
                    # Update display_name for existing users
                    self.invite_data[inviter_id][
                        "display_name"
                    ] = invite.inviter.display_name

                if invite.code not in self.invite_data[inviter_id]["active_invites"]:
                    self.invite_data[inviter_id]["active_invites"][invite.code] = 0
                self.invite_data[inviter_id]["active_invites"][
                    invite.code
                ] = invite.uses

            # Ensure all existing users have display_name fields
            await self.ensure_all_display_names(guild)

            FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)
            FileManager.write_json_file(INVITES_JSON, self.invites)
            Logger.log("Initial invite tracking data saved successfully")

        except Exception as e:
            Logger.log(f"Error initializing invites for guild {guild.name}: {str(e)}")

    def record_user(self, inviter_member, invited_member):
        """Record a user invitation in the invite data."""
        inviter_id = str(inviter_member.id)
        invited_id = str(invited_member.id)

        if inviter_id not in self.invite_data:
            self.invite_data[inviter_id] = {
                "username": str(inviter_member),
                "display_name": inviter_member.display_name,  # Add display_name
                "active_invites": {},
                "successful_invites": 0,
                "recruitment_ledger": [],
            }
        else:
            # Update display_name for existing user
            self.invite_data[inviter_id]["display_name"] = inviter_member.display_name

        # Add to recruitment ledger
        recruitment_entry = {
            "user_id": invited_id,
            "username": str(invited_member),
            "display_name": invited_member.display_name,  # Add display_name
            "initiation_date": datetime.utcnow().isoformat(),
        }

        self.invite_data[inviter_id]["recruitment_ledger"].append(recruitment_entry)
        self.invite_data[inviter_id]["successful_invites"] += 1
        FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)


def normalize_user_structure(self):
    """Normalize the structure of all user objects to have consistent field ordering."""
    for user_id, user_data in self.invite_data.items():
        # Create a new ordered dictionary with the desired field order
        normalized_data = {
            "username": user_data.get("username", "Unknown"),
            "display_name": user_data.get(
                "display_name", user_data.get("username", "Unknown")
            ),
            "active_invites": user_data.get("active_invites", {}),
            "successful_invites": user_data.get("successful_invites", 0),
            "recruitment_ledger": user_data.get("recruitment_ledger", []),
        }

        # Replace the old data with the normalized structure
        self.invite_data[user_id] = normalized_data

    # Save the normalized data
    FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)
    Logger.log("User data structure normalized successfully")


class InviteBot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.invite_manager = InviteManager()

    async def on_ready(self):
        """Event handler that executes when the bot comes online."""
        Logger.log(f"Bot connected as {self.user.name} (ID: {self.user.id})")
        Logger.log(f"Connected to {len(self.guilds)} guild(s)")

        try:
            synced = await self.tree.sync()
            Logger.log(f"Successfully synced {len(synced)} slash commands:")
            for cmd in synced:
                Logger.log(f" - /{cmd.name}")
        except Exception as e:
            Logger.log(f"Error syncing slash commands: {str(e)}")

        for guild in self.guilds:
            await self.invite_manager.initialize_invites(guild)
            # Ensure all users have display_name fields on startup
            await self.invite_manager.ensure_all_display_names(guild)

    async def on_invite_create(self, invite):
        """Handle new invite creation."""
        Logger.log(f"New invite {invite.code} created in guild {invite.guild.name}")

        # FIRST: Check if the invite has an inviter
        if not invite.inviter:
            Logger.log(
                f"Invite {invite.code} has no associated inviter, skipping tracking"
            )
            return

        inviter_id = str(invite.inviter.id)
        Logger.log(f"Invite created by user ID {inviter_id}")

        # SECOND: Ensure the inviter exists in invite_data
        if inviter_id not in self.invite_manager.invite_data:
            self.invite_manager.invite_data[inviter_id] = {
                "username": str(invite.inviter),
                "display_name": invite.inviter.display_name,  # Add display_name
                "active_invites": {},
                "successful_invites": 0,
                "recruitment_ledger": [],
            }
            Logger.log(f"Created new entry for inviter ID {inviter_id}")
        else:
            # Update display_name for existing user
            self.invite_manager.invite_data[inviter_id][
                "display_name"
            ] = invite.inviter.display_name

        # THIRD: Add the invite to the inviter's active_invites
        if (
            invite.code
            not in self.invite_manager.invite_data[inviter_id]["active_invites"]
        ):
            self.invite_manager.invite_data[inviter_id]["active_invites"][
                invite.code
            ] = 0
            Logger.log(
                f"Added invite {invite.code} to {invite.inviter.display_name}'s active invites"
            )
        else:
            Logger.log(
                f"Invite {invite.code} already exists in {invite.inviter.display_name}'s active invites"
            )

        # FOURTH: Write the updated invite_data back to invite_data.json
        FileManager.write_json_file(INVITE_DATA_JSON, self.invite_manager.invite_data)

        # FIFTH: Add to invites list for general tracking
        invite_data_entry = {
            "code": invite.code,
            "inviter_id": invite.inviter.id,
            "inviter_display_name": invite.inviter.display_name,
            "channel_id": invite.channel.id,
            "created_at": datetime.utcnow().isoformat(),
            "max_uses": invite.max_uses,
            "temporary": invite.temporary,
            "uses": invite.uses,
        }

        self.invite_manager.invites.append(invite_data_entry)
        FileManager.write_json_file(INVITES_JSON, self.invite_manager.invites)
        Logger.log(f"New invite {invite.code} created and added to tracking")

    async def on_invite_delete(self, invite):
        """Handle invite deletion by removing from all tracking data structures."""
        Logger.log(f"Invite {invite.code} deleted from guild {invite.guild.name}")

        # FIRST: Get the inviter_id from our invites data BEFORE removing anything
        inviter_id = None
        for entry in self.invite_manager.invites:
            if entry["code"] == invite.code:
                inviter_id = str(entry["inviter_id"])
                Logger.log(f"Found inviter ID {inviter_id} for invite {invite.code}")
                break

        # SECOND: Remove from active_invites using the retrieved inviter_id
        if inviter_id:
            Logger.log(f"Checking active invites for inviter ID {inviter_id}")

            if inviter_id in self.invite_manager.invite_data:
                Logger.log(
                    f"Current invite data for inviter {inviter_id}: {self.invite_manager.invite_data[inviter_id]}"
                )

                if (
                    invite.code
                    in self.invite_manager.invite_data[inviter_id]["active_invites"]
                ):
                    del self.invite_manager.invite_data[inviter_id]["active_invites"][
                        invite.code
                    ]
                    Logger.log(
                        f"Removed active invite {invite.code} from {self.invite_manager.invite_data[inviter_id]['username']}'s active invites"
                    )

                    # Write the updated invite_data back to invite_data.json
                    FileManager.write_json_file(
                        INVITE_DATA_JSON, self.invite_manager.invite_data
                    )
                else:
                    Logger.log(
                        f"Invite {invite.code} not found in {self.invite_manager.invite_data[inviter_id]['username']}'s active invites"
                    )
            else:
                Logger.log(f"Inviter ID {inviter_id} not found in invite_data")
        else:
            Logger.log(f"No inviter ID found for invite {invite.code} in our records")

        # THIRD: Remove from guild cache
        if invite.guild.id in self.invite_manager.guild_invite_caches:
            if invite.code in self.invite_manager.guild_invite_caches[invite.guild.id]:
                del self.invite_manager.guild_invite_caches[invite.guild.id][
                    invite.code
                ]
                Logger.log(f"Removed invite {invite.code} from guild cache")

        # FOURTH: Remove from invites (this should happen last)
        original_count = len(self.invite_manager.invites)
        self.invite_manager.invites = [
            entry
            for entry in self.invite_manager.invites
            if entry["code"] != invite.code
        ]

        if len(self.invite_manager.invites) < original_count:
            Logger.log(f"Removed invite {invite.code} from invite_manager.invites")
            FileManager.write_json_file(INVITES_JSON, self.invite_manager.invites)

    async def on_member_join(self, member):
        """Handle new member joining the guild."""
        guild = member.guild
        Logger.log(
            f"\nMember joined: {member} (ID: {member.id}) in guild: {guild.name}"
        )

        try:
            await self.invite_manager.validate_invites(guild)
            current_invites = await guild.invites()

            for invite in current_invites:
                if (
                    self.invite_manager.guild_invite_caches[guild.id].get(
                        invite.code, 0
                    )
                    < invite.uses
                ):
                    for saved_invite in self.invite_manager.invites:
                        if saved_invite["code"] == invite.code:
                            inviter = guild.get_member(saved_invite["inviter_id"])

                            if inviter:
                                Logger.log(
                                    f"{member.display_name} joined using invite {invite.code} created by {inviter.display_name}"
                                )
                                inviter_id = str(inviter.id)

                                if inviter_id not in self.invite_manager.invite_data:
                                    self.invite_manager.invite_data[inviter_id] = {
                                        "username": str(inviter),
                                        "display_name": inviter.display_name,  # Add display_name
                                        "active_invites": {},
                                        "successful_invites": 0,
                                        "recruitment_ledger": [],
                                    }
                                else:
                                    # Update display_name for existing user
                                    self.invite_manager.invite_data[inviter_id][
                                        "display_name"
                                    ] = inviter.display_name

                                self.invite_manager.record_user(inviter, member)
                                self.invite_manager.guild_invite_caches[guild.id][
                                    invite.code
                                ] = invite.uses
                                break

        except Exception as e:
            Logger.log(f"Error tracking member join for {member}: {str(e)}")

    async def on_member_update(self, before, after):
        """Handle member updates including display name changes."""
        try:
            # Check if display name changed
            if before.display_name != after.display_name:
                user_id = str(after.id)

                # Update in invite_data if user exists
                if user_id in self.invite_manager.invite_data:
                    old_display_name = self.invite_manager.invite_data[user_id].get(
                        "display_name", before.display_name
                    )
                    self.invite_manager.invite_data[user_id][
                        "display_name"
                    ] = after.display_name

                    # Update in recruitment_ledger entries where this user appears
                    for other_user_data in self.invite_manager.invite_data.values():
                        for recruit in other_user_data.get("recruitment_ledger", []):
                            if recruit["user_id"] == user_id:
                                recruit["display_name"] = after.display_name

                    FileManager.write_json_file(
                        INVITE_DATA_JSON, self.invite_manager.invite_data
                    )
                    Logger.log(
                        f"Updated display name for {user_id}: '{old_display_name}' -> '{after.display_name}'"
                    )

        except Exception as e:
            Logger.log(f"Error handling member update for {after}: {str(e)}")


# Create bot instance
bot = InviteBot(command_prefix=command_prefix, intents=intents)


@bot.command(name="mystats")
async def my_stats(ctx):
    """Display the invite statistics for the user who invoked the command."""
    user_id = str(ctx.author.id)

    # Update display name for the requesting user
    await bot.invite_manager.update_user_display_name(user_id, ctx.guild)

    if user_id in bot.invite_manager.invite_data:
        user_data = bot.invite_manager.invite_data[user_id]
        username = user_data["username"]
        display_name = user_data.get("display_name", ctx.author.display_name)
        successful_invites = user_data["successful_invites"]
        active_invites = user_data["active_invites"]

        embed = discord.Embed(
            title=f"📊 Invite Stats for {display_name}", color=discord.Color.blue()
        )
        embed.add_field(name="Username", value=f"`{username}`", inline=True)
        embed.add_field(name="Display Name", value=f"`{display_name}`", inline=True)
        embed.add_field(
            name="Successful Invites", value=f"**{successful_invites}**", inline=True
        )
        embed.add_field(
            name="Active Invites", value=f"**{len(active_invites)}**", inline=True
        )

        if active_invites:
            active_invite_list = []
            for code, uses in active_invites.items():
                active_invite_list.append(f"`{code}` - {uses} uses")
            embed.add_field(
                name="Active Invite Codes",
                value="\n".join(active_invite_list) if active_invite_list else "None",
                inline=False,
            )

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.avatar.url if ctx.author.avatar else None,
        )

    else:
        embed = discord.Embed(
            title="📊 No Invite Data Found",
            description=f"No invite statistics found for {ctx.author.display_name}. Create an invite to start tracking!",
            color=discord.Color.orange(),
        )

    await ctx.send(embed=embed)


@bot.command(name="normalize_structure")
@commands.has_permissions(administrator=True)
async def normalize_structure(ctx):
    """Normalize the field ordering in invite_data.json"""
    try:
        bot.invite_manager.normalize_user_structure()
        embed = discord.Embed(
            title="✅ Structure Normalized",
            description="All user objects now have consistent field ordering.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)
    except Exception as e:
        Logger.log(f"Error normalizing structure: {str(e)}")
        embed = discord.Embed(
            title="❌ Error",
            description="Failed to normalize structure.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)


@bot.command(name="leaderboard")
async def leaderboard(ctx):
    """Display the top inviters in the server."""
    if not bot.invite_manager.invite_data:
        embed = discord.Embed(
            title="📊 Invite Leaderboard",
            description="No invite data available yet.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)
        return

    # Sort users by successful invites (descending order)
    sorted_users = sorted(
        bot.invite_manager.invite_data.items(),
        key=lambda x: x[1]["successful_invites"],
        reverse=True,
    )

    embed = discord.Embed(
        title="🏆 Invite Leaderboard",
        description="Top inviters in the server",
        color=discord.Color.gold(),
    )

    for i, (user_id, user_data) in enumerate(sorted_users[:10], 1):
        username = user_data["username"]
        display_name = user_data.get("display_name", username)
        successful_invites = user_data["successful_invites"]
        active_invites = len(user_data["active_invites"])

        # Try to get current member to update display name
        member = ctx.guild.get_member(int(user_id))
        if member:
            display_name = member.display_name
            # Update the stored display name
            user_data["display_name"] = display_name

        if successful_invites > 0 or active_invites > 0:
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            embed.add_field(
                name=f"{medal} {display_name}",
                value=f"**{successful_invites}** successful invites\n**{active_invites}** active invites",
                inline=True,
            )

    # Save any updated display names
    FileManager.write_json_file(INVITE_DATA_JSON, bot.invite_manager.invite_data)

    embed.set_footer(
        text=f"Requested by {ctx.author.display_name}",
        icon_url=ctx.author.avatar.url if ctx.author.avatar else None,
    )
    await ctx.send(embed=embed)


@bot.command(name="refresh_names")
@commands.has_permissions(administrator=True)
async def refresh_display_names(ctx):
    """Manually refresh all display names from Discord."""
    try:
        updated = await bot.invite_manager.ensure_all_display_names(ctx.guild)

        if updated:
            embed = discord.Embed(
                title="✅ Display Names Updated",
                description="All display names have been refreshed from Discord.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="ℹ️ No Updates Needed",
                description="All display names are already up to date.",
                color=discord.Color.blue(),
            )

        await ctx.send(embed=embed)

    except Exception as e:
        Logger.log(f"Error refreshing display names: {str(e)}")
        embed = discord.Embed(
            title="❌ Error",
            description="Failed to refresh display names.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)


@bot.tree.command(name="mystats", description="View your invite statistics")
async def slash_my_stats(interaction: discord.Interaction):
    """Slash command version of mystats."""
    user_id = str(interaction.user.id)

    # Update display name for the requesting user
    await bot.invite_manager.update_user_display_name(user_id, interaction.guild)

    if user_id in bot.invite_manager.invite_data:
        user_data = bot.invite_manager.invite_data[user_id]
        username = user_data["username"]
        display_name = user_data.get("display_name", interaction.user.display_name)
        successful_invites = user_data["successful_invites"]
        active_invites = user_data["active_invites"]

        embed = discord.Embed(
            title=f"📊 Invite Stats for {display_name}", color=discord.Color.blue()
        )
        embed.add_field(name="Username", value=f"`{username}`", inline=True)
        embed.add_field(name="Display Name", value=f"`{display_name}`", inline=True)
        embed.add_field(
            name="Successful Invites", value=f"**{successful_invites}**", inline=True
        )
        embed.add_field(
            name="Active Invites", value=f"**{len(active_invites)}**", inline=True
        )

        if active_invites:
            active_invite_list = []
            for code, uses in active_invites.items():
                active_invite_list.append(f"`{code}` - {uses} uses")
            embed.add_field(
                name="Active Invite Codes",
                value="\n".join(active_invite_list) if active_invite_list else "None",
                inline=False,
            )

        embed.set_footer(
            text=f"Requested by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
        )

    else:
        embed = discord.Embed(
            title="📊 No Invite Data Found",
            description=f"No invite statistics found for {interaction.user.display_name}. Create an invite to start tracking!",
            color=discord.Color.orange(),
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="View the invite leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    """Slash command version of leaderboard."""
    if not bot.invite_manager.invite_data:
        embed = discord.Embed(
            title="📊 Invite Leaderboard",
            description="No invite data available yet.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)
        return

    # Sort users by successful invites (descending order)
    sorted_users = sorted(
        bot.invite_manager.invite_data.items(),
        key=lambda x: x[1]["successful_invites"],
        reverse=True,
    )

    embed = discord.Embed(
        title="🏆 Invite Leaderboard",
        description="Top inviters in the server",
        color=discord.Color.gold(),
    )

    for i, (user_id, user_data) in enumerate(sorted_users[:10], 1):
        username = user_data["username"]
        display_name = user_data.get("display_name", username)
        successful_invites = user_data["successful_invites"]
        active_invites = len(user_data["active_invites"])

        # Try to get current member to update display name
        member = interaction.guild.get_member(int(user_id))
        if member:
            display_name = member.display_name
            # Update the stored display name
            user_data["display_name"] = display_name

        if successful_invites > 0 or active_invites > 0:
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            embed.add_field(
                name=f"{medal} {display_name}",
                value=f"**{successful_invites}** successful invites\n**{active_invites}** active invites",
                inline=True,
            )

    # Save any updated display names
    FileManager.write_json_file(INVITE_DATA_JSON, bot.invite_manager.invite_data)

    embed.set_footer(
        text=f"Requested by {interaction.user.display_name}",
        icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
    )
    await interaction.response.send_message(embed=embed)


# Run the bot
if __name__ == "__main__":
    if not bot_token:
        Logger.log("Error: Bot token not found in config.json")
    else:
        Logger.log("Starting Discord Invite Tracker Bot...")
        bot.run(bot_token)
