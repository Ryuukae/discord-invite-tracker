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
                        "uses": invite.uses,
                        "temporary": invite.temporary,
                    }
                    self.invites.append(invite_entry)
                    Logger.log(f"Added existing invite {invite.code} to invites.json")

                if not invite.inviter:
                    continue

                inviter_id = str(invite.inviter.id)
                if inviter_id not in self.invite_data:
                    self.invite_data[inviter_id] = {
                        "username": str(invite.inviter),
                        "display_name": invite.inviter.display_name,
                        "active_invites": {},
                        "successful_invites": 0,
                        "recruitment_ledger": [],
                    }

                if invite.code not in self.invite_data[inviter_id]["active_invites"]:
                    self.invite_data[inviter_id]["active_invites"][invite.code] = 0
                self.invite_data[inviter_id]["active_invites"][
                    invite.code
                ] = invite.uses

            FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)
            FileManager.write_json_file(INVITES_JSON, self.invites)
            Logger.log("Initial invite tracking data saved successfully")

        except Exception as e:
            Logger.log(f"Error initializing invites for guild {guild.name}: {str(e)}")

    async def ensure_all_display_names(self, guild):
        """Ensure all display names are up to date."""
        updated = False
        for user_id, user_data in self.invite_data.items():
            member = guild.get_member(int(user_id))
            if member:
                if member.display_name != user_data["display_name"]:
                    user_data["display_name"] = member.display_name
                    updated = True
        if updated:
            FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)
        return updated


class InviteBot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.invite_manager = InviteManager()

    async def on_ready(self):
        """Event handler that executes when the bot comes online."""
        Logger.log(f"Bot connected as {self.user.name} (ID: {self.user.id})")
        Logger.log(f"Connected to {len(self.guilds)} guild(s): {self.guilds}")

        try:
            synced = await self.tree.sync()
            Logger.log(f"Successfully synced {len(synced)} slash commands:")
            for cmd in synced:
                Logger.log(f" - /{cmd.name}")
        except Exception as e:
            Logger.log(f"Error syncing slash commands: {str(e)}")

        for guild in self.guilds:
            await self.invite_manager.initialize_invites(guild)

        Logger.log("status: listening for activity...")

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
        Logger.log(
            f"Invite {invite.code} created by user {invite.inviter.display_name}"
        )

        # SECOND: Ensure the inviter exists in invite_data
        if inviter_id not in self.invite_manager.invite_data:
            self.invite_manager.invite_data[inviter_id] = {
                "username": str(invite.inviter),
                "display_name": invite.inviter.display_name,
                "active_invites": {},
                "successful_invites": 0,
                "recruitment_ledger": [],
            }

            Logger.log(f"Created new entry for inviter ID {inviter_id}")

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
            "uses": invite.uses,
            "max_uses": invite.max_uses,
            "temporary": invite.temporary,
        }

        self.invite_manager.invites.append(invite_data_entry)
        FileManager.write_json_file(INVITES_JSON, self.invite_manager.invites)
        Logger.log(
            f"New invite {invite.code} was logged, added to tracking, and was written/saved in invites.json"
        )

    async def on_invite_delete(self, invite):
        """Handle invite deletion by removing from all tracking data structures."""
        Logger.log(f"Invite {invite.code} deleted from guild {invite.guild.name}")

        # FIRST: Get the inviter_id from our invites data BEFORE removing anything
        inviter_id = None
        for entry in self.invite_manager.invites:
            if entry["code"] == invite.code:
                inviter_id = str(
                    entry["inviter_id"]
                )  # Get the inviter_id from the invites.json entry
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
            # Write the updated invites back to invites.json
            FileManager.write_json_file(INVITES_JSON, self.invite_manager.invites)


async def on_member_join(self, member):
    """Handle new member joining the guild."""
    guild = member.guild
    Logger.log(f"\nMember joined: {member} (ID: {member.id}) in guild: {guild.name}")

    try:
        await self.invite_manager.validate_invites(guild)
        current_invites = await guild.invites()

        for invite in current_invites:
            if (
                self.invite_manager.guild_invite_caches[guild.id].get(invite.code, 0)
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
                                    "display_name": invite.inviter.display_name,
                                    "active_invites": {},
                                    "successful_invites": 0,
                                    "recruitment_ledger": [],
                                }

                            if (
                                invite.code
                                not in self.invite_manager.invite_data[inviter_id][
                                    "active_invites"
                                ]
                            ):
                                self.invite_manager.invite_data[inviter_id][
                                    "active_invites"
                                ][invite.code] = 0
                            self.invite_manager.invite_data[inviter_id][
                                "active_invites"
                            ][invite.code] += 1
                            previous_count = self.invite_manager.invite_data[
                                inviter_id
                            ]["successful_invites"]
                            self.invite_manager.invite_data[inviter_id][
                                "successful_invites"
                            ] += 1
                            new_count = self.invite_manager.invite_data[inviter_id][
                                "successful_invites"
                            ]

                            if (
                                new_count in [5, 10, 15, 20, 25, 30, 50]
                                and previous_count < new_count
                            ):
                                try:
                                    owner = guild.owner
                                    milestone_message = (
                                        f"Milestone Alert!\n"
                                        f"User  {inviter.display_name} has reached {new_count} successful invites!\n"
                                        f"Latest recruit: {member.display_name}"
                                    )
                                    await owner.send(milestone_message)
                                    Logger.log(
                                        f"Sent milestone notification to server owner for {inviter.display_name}"
                                    )
                                except Exception as e:
                                    Logger.log(
                                        f"Failed to send milestone notification: {str(e)}"
                                    )

                            # Check for unique user_id before adding to recruitment_ledger
                            recruited_member = {
                                "user_id": str(member.id),
                                "username": str(member),
                                "display_name": str(member.display_name),
                                "initiation_date": datetime.utcnow().isoformat(),
                                "invite_code": invite.code
                            }

                            # Ensure unique user_id in recruitment_ledger across all members
                            if not any(
                                entry["user_id"] == recruited_member["user_id"]
                                for inviter_data in self.invite_manager.invite_data.values()
                                for entry in inviter_data["recruitment_ledger"]
                            ):
                                self.invite_manager.invite_data[inviter_id][
                                    "recruitment_ledger"
                                ].append(recruited_member)
                                Logger.log(
                                    f"Added {member.display_name} to {inviter.display_name}'s recruitment ledger."
                                )

                            FileManager.write_json_file(
                                INVITE_DATA_JSON, self.invite_manager.invite_data
                            )

                            for invite_entry in self.invite_manager.invites:
                                if invite_entry["code"] == invite.code:
                                    invite_entry["uses"] += 1
                                    Logger.log(
                                        f"Updated invite {invite.code} usage to {invite_entry['uses']} in invites.json"
                                    )
                                    break

                            FileManager.write_json_file(
                                INVITES_JSON, self.invite_manager.invites
                            )
                            self.invite_manager.guild_invite_caches[guild.id][
                                invite.code
                            ] = invite.uses
                            break

    except discord.Forbidden:
        Logger.log(f"Cannot check invites in {guild.name} - missing permissions")


# Bot setup
invite_bot = InviteBot(command_prefix=command_prefix, intents=intents)


# Command Definitions
@invite_bot.tree.command(
    name="invites", description="[ADMIN] Check your or another member's invite stats"
)
@commands.has_permissions(administrator=True)
async def invites(
    interaction: discord.Interaction, member: discord.Member = None, verbose: int = 0
):
    # Check if the user is the server owner
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
        return

    target = member or interaction.user
    target_id = str(target.id)

    Logger.log(
        f"\n{interaction.user} used the /invites command for {target} (verbose level: {verbose})"
    )

    if target_id in invite_bot.invite_manager.invite_data:
        data = invite_bot.invite_manager.invite_data[target_id]
        active = 0

        try:
            current_invites = await interaction.guild.invites()
            current_codes = [invite.code for invite in current_invites]

            active_invites = {
                code: times_used
                for code, times_used in data["active_invites"].items()
                if code in current_codes
            }
            active = sum(active_invites.values())

            # Basic response
            response = (
                f"**{target.display_name}'s Invite Stats:**\n"
                f"• Total successful invites: {data['successful_invites']}\n"
                f"• Recruited members: {', '.join(member['display_name'] for member in data['recruitment_ledger']) if data['recruitment_ledger'] else 'None'}"
            )

            # Verbose level 1
            if verbose == 1:
                response += f"\n\n**📊 Detailed Statistics:**"
                response += f"\n• Active invite codes: {len(active_invites)}"
                response += f"\n• Total uses from active invites: {active}"
                response += f"\n• Total recruitment ledger entries: {len(data['recruitment_ledger'])}"

                if active_invites:
                    response += f"\n\n**🔗 Active Invite Breakdown:**"
                    for code, uses in active_invites.items():
                        response += f"\n• `{code}`: {uses} uses"

                if data.get("recruitment_ledger"):
                    response += f"\n\n**👥 Recruitment Details:**"
                    for i, recruited_member in enumerate(
                        data["recruitment_ledger"][:5], 1
                    ):  # Limit to first 5
                        join_date = recruited_member.get("initiation_date", "Unknown")
                        if join_date != "Unknown":
                            try:
                                join_date = datetime.fromisoformat(
                                    join_date.replace("Z", "")
                                ).strftime("%Y-%m-%d %H:%M")
                            except:
                                pass
                        response += f"\n• {recruited_member['display_name']} (ID: {recruited_member['user_id']}, Joined: {join_date})"

                    if len(data["recruitment_ledger"]) > 5:
                        response += (
                            f"\n• ... and {len(data['recruitment_ledger']) - 5} more"
                        )

            # Verbose level 2 - Full details from both files
            elif verbose == 2:
                # Include all verbose level 1 information
                response += f"\n\n**📊 Detailed Statistics:**"
                response += f"\n• Active invite codes: {len(active_invites)}"
                response += f"\n• Total uses from active invites: {active}"
                response += f"\n• Total recruitment ledger entries: {len(data['recruitment_ledger'])}"

                if active_invites:
                    response += f"\n\n**🔗 Active Invite Breakdown:**"
                    for code, uses in active_invites.items():
                        response += f"\n• `{code}`: {uses} uses"

                if data.get("recruitment_ledger"):
                    response += f"\n\n**👥 Recruitment Details:**"
                    for recruited_member in data["recruitment_ledger"]:
                        join_date = recruited_member.get("initiation_date", "Unknown")
                        if join_date != "Unknown":
                            try:
                                join_date = datetime.fromisoformat(
                                    join_date.replace("Z", "")
                                ).strftime("%Y-%m-%d %H:%M")
                            except:
                                pass
                        response += f"\n• {recruited_member['display_name']} (ID: {recruited_member['user_id']}, Joined: {join_date})"

                # Complete invite_data.json information
                response += f"\n\n**📋 Complete Invite Data (invite_data.json):**"
                response += f"\n• Username: {data.get('username', 'N/A')}"
                response += f"\n• Display Name: {data.get('display_name', 'N/A')}"
                response += (
                    f"\n• Successful Invites: {data.get('successful_invites', 0)}"
                )

                all_active = data.get("active_invites", {})
                response += f"\n• All Active Invites: {len(all_active)}"
                if all_active:
                    response += f"\n\n**🔗 All Invite Codes & Usage:**"
                    for code, uses in all_active.items():
                        status = "Active" if code in current_codes else "Inactive"
                        response += f"\n• `{code}`: {uses} uses ({status})"

                # Additional information from invites.json
                invite_details = load_additional_invite_data(target_id, target.id)
                if invite_details:
                    response += (
                        f"\n\n**📄 Detailed Invite Information (invites.json):**"
                    )
                    response += f"\n• Total invite codes created: {len(invite_details)}"

                    if invite_details:
                        response += f"\n\n**📝 Individual Invite Details:**"
                        for i, invite_info in enumerate(
                            invite_details[:10], 1
                        ):  # Show first 10 detailed invites
                            response += f"\n**Invite #{i}:**"
                            response += (
                                f"\n  • Code: `{invite_info.get('code', 'N/A')}`"
                            )
                            response += f"\n  • Channel ID: {invite_info.get('channel_id', 'N/A')}"
                            response += (
                                f"\n  • Created: {invite_info.get('created_at', 'N/A')}"
                            )
                            response += f"\n  • Max Uses: {invite_info.get('max_uses', 'Unlimited')}"
                            response += (
                                f"\n  • Current Uses: {invite_info.get('uses', 0)}"
                            )
                            response += f"\n  • Temporary: {'Yes' if invite_info.get('temporary', False) else 'No'}"

                        if len(invite_details) > 10:
                            response += f"\n• ... and {len(invite_details) - 10} more invite codes"

                        # Summary statistics
                        total_uses = sum(
                            invite.get("uses", 0) for invite in invite_details
                        )
                        temporary_count = sum(
                            1
                            for invite in invite_details
                            if invite.get("temporary", False)
                        )
                        permanent_count = len(invite_details) - temporary_count

                        response += f"\n\n**📊 Invite Summary:**"
                        response += f"\n• Total Successful Invites: {total_uses}"
                        response += f"\n  -------------------------"
                        response += f"\n• Permanent invites: {permanent_count}"
                        response += f"\n• Temporary invites: {temporary_count}"
                        response += f"\n\n• Average uses per invite: {round(total_uses / len(invite_details), 2) if invite_details else 0}"
                else:
                    response += (
                        f"\n\n**📄 Detailed Invite Information (invites.json):**"
                    )
                    response += (
                        f"\n• No additional invite details found in invites.json"
                    )

            Logger.log(
                f"Returning invite stats for {target.display_name} (verbose: {verbose})"
            )
        except Exception as e:
            Logger.log(f"Error fetching invites: {str(e)}")
            response = "❌ Error fetching invite data. Please try again later."
    else:
        response = f"{target.display_name} hasn't created any trackable invites yet."
        Logger.log(f"No invite data found for {target.display_name}")

    await interaction.response.send_message(response, ephemeral=True)


def load_additional_invite_data(user_id_str, user_id_int):
    """
    Load additional invite data from invites.json file for a specific user

    Args:
        user_id_str (str): The user ID as string
        user_id_int (int): The user ID as integer

    Returns:
        list: List of invite details for the user, or empty list if not found
    """
    try:
        # Load the invites.json data
        invites_data = invite_bot.invite_manager.invites

        # Find all invites created by this user
        user_invites = []
        for invite_entry in invites_data:
            # Check both string and integer versions of user ID
            if (
                str(invite_entry.get("inviter_id")) == user_id_str
                or invite_entry.get("inviter_id") == user_id_int
            ):
                user_invites.append(invite_entry)

        Logger.log(f"Found {len(user_invites)} invite entries for user {user_id_str}")
        return user_invites

    except Exception as e:
        Logger.log(f"Error loading additional invite data: {str(e)}")
        return []


# Command Definitions
@invite_bot.tree.command(name="mystats", description="Check your invite stats")
async def mystats(interaction: discord.Interaction):
    target = interaction.user  # Use the user who called the command
    target_id = str(target.id)

    Logger.log(f"\n{interaction.user} used the /mystats command")

    if target_id in invite_bot.invite_manager.invite_data:
        data = invite_bot.invite_manager.invite_data[target_id]
        active = 0

        try:
            current_invites = await interaction.guild.invites()
            current_codes = [invite.code for invite in current_invites]

            active_invites = {
                code: times_used
                for code, times_used in data["active_invites"].items()
                if code in current_codes
            }
            active = sum(active_invites.values())

            response = (
                f"**{target.display_name}'s Invite Stats:**\n"
                f"• Total successful invites: {data['successful_invites']}\n"
                f"• Recruited members: {', '.join(member['display_name'] for member in data['recruitment_ledger']) if data['recruitment_ledger'] else 'None'}"
            )
            Logger.log(f"Returning invite stats for {target.display_name}")
        except Exception as e:
            Logger.log(f"Error fetching invites: {str(e)}")
            response = "❌ Error fetching invite data. Please try again later."
    else:
        response = f"{target.display_name} hasn't created any trackable invites yet."
        Logger.log(f"No invite data found for {target.display_name}")

    await interaction.response.send_message(response, ephemeral=True)


@invite_bot.tree.command(
    name="invite_leaderboard", description="Display the top inviters in the server."
)
async def leaderboard(interaction: discord.Interaction):
    """Display the top inviters in the server."""
    if not invite_bot.invite_manager.invite_data:
        embed = discord.Embed(
            title="📊 Invite Leaderboard",
            description="No invite data available yet.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)
        return

    # Sort users by successful invites (descending order)
    sorted_users = sorted(
        invite_bot.invite_manager.invite_data.items(),
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
    FileManager.write_json_file(INVITE_DATA_JSON, invite_bot.invite_manager.invite_data)
    FileManager.write_json_file(INVITES_JSON, invite_bot.invite_manager.invites)

    embed.set_footer(
        text=f"Requested by {interaction.user.display_name}",
        icon_url=interaction.user.avatar.url if interaction.user.avatar else None,
    )
    await interaction.response.send_message(embed=embed)


@invite_bot.tree.command(
    name="refresh_names", description="Manually refresh all display names from Discord."
)
@commands.has_permissions(administrator=True)
async def refresh_display_names(interaction: discord.Interaction):
    """Manually refresh all display names from Discord."""
    try:
        updated = await invite_bot.invite_manager.ensure_all_display_names(
            interaction.guild
        )

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

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        Logger.log(f"Error refreshing display names: {str(e)}")
        embed = discord.Embed(
            title="❌ Error",
            description="Failed to refresh display names.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)


# Run the bot
if __name__ == "__main__":
    Logger.log("Starting bot...")
    invite_bot.run(bot_token)
