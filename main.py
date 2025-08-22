import discord
from discord.ext import commands
import json
import os
from datetime import datetime

# Constants
INVITE_DATA_JSON = 'invite_data.json'
INVITES_JSON = 'invites.json'
CONFIG_JSON = 'config.json'

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
            with open(file_path, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def write_json_file(file_path: str, data) -> None:
        """Write data to a JSON file."""
        try:
            with open(file_path, 'w') as f:
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
            active_invites = data['active_invites']
            for invite_code in list(active_invites.keys()):
                if invite_code not in {invite.code for invite in current_invites}:
                    del active_invites[invite_code]
                    Logger.log(f"Removed inactive invite {invite_code} from {data['username']}'s active invites")

        FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)

    async def initialize_invites(self, guild):
        """Initialize invite tracking for a guild."""
        Logger.log(f"\nInitializing invites for guild: {guild.name} (ID: {guild.id})")
        self.guild_invite_caches[guild.id] = {}
        await self.validate_invites(guild)

        try:
            invites = await guild.invites()
            Logger.log(f"Found {len(invites)} existing invites")
            existing_codes = {invite_entry['code'] for invite_entry in self.invites}

            for invite in invites:
                self.guild_invite_caches[guild.id][invite.code] = invite.uses

                if invite.code not in existing_codes and invite.inviter:
                    invite_entry = {
                        "code": invite.code,
                        "inviter_id": invite.inviter.id,
                        "inviter_display_name": invite.inviter.display_name,
                        "channel_id": invite.channel.id,
                        "created_at": invite.created_at.isoformat() if invite.created_at else datetime.utcnow().isoformat(),
                        "max_uses": invite.max_uses,
                        "temporary": invite.temporary,
                        "uses": invite.uses
                    }
                    self.invites.append(invite_entry)
                    Logger.log(f"Added existing invite {invite.code} to invites.json")

                if not invite.inviter:
                    continue

                inviter_id = str(invite.inviter.id)
                if inviter_id not in self.invite_data:
                    self.invite_data[inviter_id] = {
                        'username': str(invite.inviter),
                        'active_invites': {},
                        'successful_invites': 0,
                        'recruitment_ledger': []
                    }

                if invite.code not in self.invite_data[inviter_id]['active_invites']:
                    self.invite_data[inviter_id]['active_invites'][invite.code] = 0
                self.invite_data[inviter_id]['active_invites'][invite.code] = invite.uses

            FileManager.write_json_file(INVITE_DATA_JSON, self.invite_data)
            FileManager.write_json_file(INVITES_JSON, self.invites)
            Logger.log("Initial invite tracking data saved successfully")

        except Exception as e:
            Logger.log(f"Error initializing invites for guild {guild.name}: {str(e)}")

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

    async def on_invite_create(self, invite):
        """Handle new invite creation."""
        Logger.log(f"New invite {invite.code} created in guild {invite.guild.name}")

        # FIRST: Check if the invite has an inviter
        if not invite.inviter:
            Logger.log(f"Invite {invite.code} has no associated inviter, skipping tracking")
            return

        inviter_id = str(invite.inviter.id)
        Logger.log(f"Invite created by user ID {inviter_id}")

        # SECOND: Ensure the inviter exists in invite_data
        if inviter_id not in self.invite_manager.invite_data:
            self.invite_manager.invite_data[inviter_id] = {
                'username': str(invite.inviter),
                'active_invites': {},
                'successful_invites': 0,
                'recruitment_ledger': []
            }
            Logger.log(f"Created new entry for inviter ID {inviter_id}")

        # THIRD: Add the invite to the inviter's active_invites
        if invite.code not in self.invite_manager.invite_data[inviter_id]['active_invites']:
            self.invite_manager.invite_data[inviter_id]['active_invites'][invite.code] = 0
            Logger.log(f"Added invite {invite.code} to {invite.inviter.display_name}'s active invites")
        else:
            Logger.log(f"Invite {invite.code} already exists in {invite.inviter.display_name}'s active invites")

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
            "uses": invite.uses
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
            if entry['code'] == invite.code:
                inviter_id = str(entry['inviter_id'])  # Get the inviter_id from the invites.json entry
                Logger.log(f"Found inviter ID {inviter_id} for invite {invite.code}")
                break

        # SECOND: Remove from active_invites using the retrieved inviter_id
        if inviter_id:
            Logger.log(f"Checking active invites for inviter ID {inviter_id}")

            if inviter_id in self.invite_manager.invite_data:
                Logger.log(f"Current invite data for inviter {inviter_id}: {self.invite_manager.invite_data[inviter_id]}")
        
                if invite.code in self.invite_manager.invite_data[inviter_id]['active_invites']:
                    del self.invite_manager.invite_data[inviter_id]['active_invites'][invite.code]
                    Logger.log(f"Removed active invite {invite.code} from {self.invite_manager.invite_data[inviter_id]['username']}'s active invites")
                
                    # Write the updated invite_data back to invite_data.json
                    FileManager.write_json_file(INVITE_DATA_JSON, self.invite_manager.invite_data)
                else:
                    Logger.log(f"Invite {invite.code} not found in {self.invite_manager.invite_data[inviter_id]['username']}'s active invites")
            else:
                Logger.log(f"Inviter ID {inviter_id} not found in invite_data")
        else:
            Logger.log(f"No inviter ID found for invite {invite.code} in our records")

        # THIRD: Remove from guild cache
        if invite.guild.id in self.invite_manager.guild_invite_caches:
            if invite.code in self.invite_manager.guild_invite_caches[invite.guild.id]:
                del self.invite_manager.guild_invite_caches[invite.guild.id][invite.code]
                Logger.log(f"Removed invite {invite.code} from guild cache")

        # FOURTH: Remove from invites (this should happen last)
        original_count = len(self.invite_manager.invites)
        self.invite_manager.invites = [entry for entry in self.invite_manager.invites if entry['code'] != invite.code]

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
                if self.invite_manager.guild_invite_caches[guild.id].get(invite.code, 0) < invite.uses:
                    for saved_invite in self.invite_manager.invites:
                        if saved_invite['code'] == invite.code:
                            inviter = guild.get_member(saved_invite['inviter_id'])

                            if inviter:
                                Logger.log(f"{member.display_name} joined using invite {invite.code} created by {inviter.display_name}")
                                inviter_id = str(inviter.id)

                                if inviter_id not in self.invite_manager.invite_data:
                                    self.invite_manager.invite_data[inviter_id] = {
                                        'username': str(inviter),
                                        'active_invites': {},
                                        'successful_invites': 0,
                                        'recruitment_ledger': []
                                    }

                                if invite.code not in self.invite_manager.invite_data[inviter_id]['active_invites']:
                                    self.invite_manager.invite_data[inviter_id]['active_invites'][invite.code] = 0
                                self.invite_manager.invite_data[inviter_id]['active_invites'][invite.code] += 1
                                previous_count = self.invite_manager.invite_data[inviter_id]['successful_invites']
                                self.invite_manager.invite_data[inviter_id]['successful_invites'] += 1
                                new_count = self.invite_manager.invite_data[inviter_id]['successful_invites']

                                if new_count in [5, 10, 15, 20, 25, 30, 50] and previous_count < new_count:
                                    try:
                                        owner = guild.owner
                                        milestone_message = (
                                            f"Milestone Alert\n"
                                            f"User     {inviter.display_name} has reached {new_count} successful invites!\n"
                                            f"Latest recruit: {member.display_name}"
                                        )
                                        await owner.send(milestone_message)
                                        Logger.log(f"Sent milestone notification to server owner for {inviter.display_name}")
                                    except Exception as e:
                                        Logger.log(f"Failed to send milestone notification: {str(e)}")

                                # Check for unique user_id before adding to recruitment_ledger
                                recruited_member = {
                                    'user_id': str(member.id),
                                    'username': str(member),
                                    'display_name': str(member.display_name),
                                    'initiation_date': datetime.utcnow().isoformat()
                                }

                                # Ensure unique user_id in recruitment_ledger
                                if not any(entry['user_id'] == recruited_member['user_id'] for entry in self.invite_manager.invite_data[inviter_id]['recruitment_ledger']):
                                    self.invite_manager.invite_data[inviter_id]['recruitment_ledger'].append(recruited_member)
                                    Logger.log(f"Added {member.display_name} to {inviter.display_name}'s recruitment ledger.")

                                FileManager.write_json_file(INVITE_DATA_JSON, self.invite_manager.invite_data)

                                for invite_entry in self.invite_manager.invites:
                                    if invite_entry['code'] == invite.code:
                                        invite_entry['uses'] += 1
                                        Logger.log(f"Updated invite {invite.code} usage to {invite_entry['uses']} in invites.json")
                                        break

                                FileManager.write_json_file(INVITES_JSON, self.invite_manager.invites)
                                self.invite_manager.guild_invite_caches[guild.id][invite.code] = invite.uses
                                break

        except discord.Forbidden:
            Logger.log(f"Cannot check invites in {guild.name} - missing permissions")

# Bot setup
invite_bot = InviteBot(command_prefix=command_prefix, intents=intents)

# Command Definitions
@invite_bot.tree.command(name="invites", description="Check your or another member's invite stats")
async def invites(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    target_id = str(target.id)

    Logger.log(f"\n/invites command used by {interaction.user} for {target}")

    if target_id in invite_bot.invite_manager.invite_data:
        data = invite_bot.invite_manager.invite_data[target_id]
        active = 0

        try:
            current_invites = await interaction.guild.invites()
            current_codes = [invite.code for invite in current_invites]

            active_invites = {
                code: times_used for code, times_used in data['active_invites'].items() 
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

@invite_bot.tree.command(name="invite_leaderboard", description="Show top inviters")
async def leaderboard(interaction: discord.Interaction):
    """Display the top 10 users by invite count."""
    Logger.log(f"\n/leaderboard command used by {interaction.user}")

    if not invite_bot.invite_manager.invite_data:
        await interaction.response.send_message("No invite data available yet.")
        return

    sorted_data = sorted(
        invite_bot.invite_manager.invite_data.items(),
        key=lambda x: x[1]['successful_invites'],
        reverse=True
    )[:10]  # Top 10

    embed = discord.Embed(
        title="Invite Leaderboard",
        color=discord.Color.green()
    )

    for index, (user_id, data) in enumerate(sorted_data, start=1):
        embed.add_field(
            name=f"{index}. {data['username']}",
            value=f"**{data['successful_invites']}** members joined",
            inline=False
        )

    Logger.log("Displaying leaderboard")
    await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    Logger.log("Starting bot...")
    invite_bot.run(bot_token)