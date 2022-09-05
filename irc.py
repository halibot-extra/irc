#
# Reference Halibot IRC Agent
#  Connects to an IRC server and relays messages to and from the base
#
from halibot import HalAgent, HalConfigurer, Message
from collections import OrderedDict
import pydle, threading, asyncio

# Haliot Reference IRC Agent
#  Creates a Pydle IRC Client and connects to a server
#  Receives messages from the server, relays them to the Halibot base
#  Receives messages from the Halibot base, relays them to the IRC server
class IrcAgent(HalAgent):

	class Configurer(HalConfigurer):
		def configure(self):
			self.optionString('nickname', prompt='Nickname', default='halibot')
			self.optionString('hostname', prompt='Server hostname', default='irc.libera.chat')
			self.optionInt('port', prompt='Server port', default=6697)
			self.optionString('channel', prompt='Channel to join')

			self.optionBoolean('tls', prompt='Enable TLS', default=True)
			if self.options['tls']:
				self.optionBoolean('tls-verify', prompt='Verify server TLS certificate', default=False)
				if self.options['tls-verify']:
					self.optionString('tls-certificate-file', prompt='TLS certificate file')
					self.optionString('tls-certificate-keyfile', prompt='TLS certificate keyfile')
					self.optionString('tls-certificate-file', prompt='TLS certificate password')

			self.optionString('sasl-username', prompt='SASL username')
			if 'sasl-username' in self.options:
				self.optionString('sasl-password', prompt='SASL password')
				self.optionString('sasl-identity', prompt='SASL identity')

	# Handle to the Pydle IRC Client object as defined below
	client = None

	# Called when the IrcAgent is instantiated.
	#  Anything needed to get the agent running should go in here,
	#  NOT in __init__()!
	def init(self):
		# Create the IRC client object as defined/extended below
		self.client = IrcClient(
				nickname                 = self.config['nickname'],
				tls_certificate_file     = self.config.get('tls-certificate-file'),
				tls_certificate_keyfile  = self.config.get('tls-certificate-keyfile'),
				tls_certificate_password = self.config.get('tls-certificate-file'),
				sasl_username            = self.config.get('sasl-username'),
				sasl_password            = self.config.get('sasl-password'),
				sasl_identity            = self.config.get('sasl-identity'),
		)

		# Give the client object a handle to talk back to this agent class
		self.client.agent = self

		self._start_client_thread()

	# Implement the receive() function as defined in the HalModule class
	#  This is called when the Halibot wants to send a message out using this agent.
	#  In this case, the logic for sending a message to the IRC channel is put here,
	#  using the whom as the "channel", which is the tail end of the resource
	#  identifier for this target (e.g. the "#foo" in "irc/#foo").
	def receive(self, msg):
		asyncio.ensure_future(self.client.message(msg.whom(), msg.body), loop=self.eventloop)

	def shutdown(self):
		asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.eventloop)
		self.thread.join()
		# Close this eventloop *after* the join, lets the disconnect() have time to finish
		#  This may not even need to be called
		self.client.eventloop.stop()

	def _run_client(self):
		self.client.run(
				hostname   = self.config['hostname'],
				port       = self.config['port'],
				tls        = self.config.get('tls', True),
				tls_verify = self.config.get('tls-verify', False),
		)

	# Start the thread the IRC client will live in
	#  This is so the client does not block on halibot's instantiation (main) thread,
	#  thus causing to stop there and never finish starting up
	def _start_client_thread(self):
		self.thread = threading.Thread(target=self._run_client)
		self.thread.start()

	# NOTE: The Module() base class implements a send() function, do NOT override this.
	#  The function is used to send to the Halibot base for module processing.
	#  Simply implementing the receive() function is enough to get messages from the modules,
	#   to get messages from your agent targer (IRC, XMPP, etc), that is up to the developer.



# Pydle IRC Client class.
#  This will handle all the IRC work, and talks to the base via the IRCAgent
#  The following is for reference, some of which will be pydle-specific
class IrcClient(pydle.Client):

	# Handle to the IRC Agent above
	agent = None

	# Cache of whois lookups
	whois_cache = {}

	# Pydle calls this when the client connects to the server.
	#  Sets the channel(s) to join from the agent's config.
	#  NOTE: the config field is automatically populated from the relevant
	#   config files when the module is loaded
	async def on_connect(self):
		await super().on_connect()

		channel = self.agent.config['channel']
		if isinstance(channel, str):
			# Is a string, join that channel
			await self.join(channel)
		else:
			# Persume a list of channels, join those
			for c in channel:
				await self.join(c)

	async def on_quit(self, channel, user):
		# Invalidate the WHOIS cache entry
		if user in self.whois_cache: self.whois_cache.pop(user)

	async def on_nick_change(self, old, new):
		# Invalidate the WHOIS cache entries
		if old in self.whois_cache: self.whois_cache.pop(old)
		if new in self.whois_cache: self.whois_cache.pop(new)

	async def identity(self, nick):
		# Do the WHOIS if the result is not cached
		if not nick in self.whois_cache:
			self.whois_cache[nick] = await self.whois(nick)

		# If they are identified, return the account name
		if self.whois_cache[nick]['identified']:
			return self.whois_cache[nick]['account']

		# Not identified
		return None

	# Pydle calls this when a message is received from the server
	#  The purpose of this agent is to communicate with IRC,
	#  so this repackages the message from Pydle into a Halibot-friendly message
	async def on_channel_message(self, target, by, text):
		org = self.agent.name + '/' + target
		msg = Message(body=text, author=by, identity=await self.identity(by), origin=org)

		# Send the Halibot-friendly message to the Halibot base for module processing
		self.agent.dispatch(msg)

	# Pydle calls this when a message is received from a single user.
	#  This is similar to on_channel_message above, except we aren't dealing with a channel
	#  Therefore, the origin is the same as the author, which is the "by" field
	async def on_private_message(self, by, text):
		org = self.agent.name + '/' + by
		msg = Message(body=text, author=by, identity=self.identity(by), origin=org)

		self.agent.dispatch(msg)
