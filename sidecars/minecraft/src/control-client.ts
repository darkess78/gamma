import { BlockList, isIP } from 'node:net';
import { TextDecoder } from 'node:util';

import { visit } from 'jsonc-parser';
import WebSocket, { type RawData } from 'ws';

import {
  PROTOCOL_NAME,
  PROTOCOL_VERSION,
  parseProtocolMessage,
  type CancelCommandMessage,
  type CommandAckMessage,
  type CommandMessage,
  type CommandProgressMessage,
  type CompanionState,
  type EmergencyStopMessage,
  type MinecraftStatusMessage,
  type ProtocolErrorMessage,
  type ShutdownMessage,
  type SidecarStatusMessage,
  type StateSnapshotMessage,
  type TerminalResultMessage,
  type WelcomeMessage
} from './protocol.js';

const CONTROL_PATH = '/v1/minecraft/control';
const DEFAULT_MAXIMUM_INBOUND_BYTES = 65_536;
const MAXIMUM_INBOUND_BYTES_LIMIT = 1_048_576;
const DEFAULT_HANDSHAKE_TIMEOUT_MS = 10_000;
const MAXIMUM_HANDSHAKE_TIMEOUT_MS = 300_000;
const UTF8_DECODER = new TextDecoder('utf-8', { fatal: true });
const IPV6_LOOPBACKS = new BlockList();

IPV6_LOOPBACKS.addAddress('::1', 'ipv6');
IPV6_LOOPBACKS.addSubnet('::ffff:127.0.0.0', 104, 'ipv6');

export type MinecraftControlClientState =
  | 'disconnected'
  | 'connecting'
  | 'awaiting_welcome'
  | 'ready'
  | 'closing';

export type MinecraftHelloIdentity = Readonly<{
  sidecarInstanceId: string;
  sidecarBuild: string;
  nodeVersion: string;
  minecraftLibraryVersion: string;
  pathfinderVersion: string;
}>;

export type MinecraftControlClientOptions = Readonly<{
  url: string;
  controlToken: string;
  hello: MinecraftHelloIdentity;
  maximumInboundBytes?: number;
  handshakeTimeoutMs?: number;
  now?: () => Date;
  createMessageId?: () => string;
}>;

export type SidecarOutboundMessage =
  | Readonly<{ type: 'sidecar_status'; payload: SidecarStatusMessage['payload'] }>
  | Readonly<{ type: 'minecraft_status'; payload: MinecraftStatusMessage['payload'] }>
  | Readonly<{
      type: 'command_ack';
      trace_id: string;
      command_id: string;
      payload: CommandAckMessage['payload'];
    }>
  | Readonly<{
      type: 'command_progress';
      trace_id: string;
      command_id: string;
      payload: CommandProgressMessage['payload'];
    }>
  | Readonly<{
      type: 'state_snapshot';
      trace_id?: string | null;
      command_id?: string | null;
      payload: StateSnapshotMessage['payload'];
    }>
  | Readonly<{
      type: 'terminal_result';
      trace_id: string;
      command_id: string;
      payload: TerminalResultMessage['payload'];
    }>;

export type GammaControlMessage =
  | CommandMessage
  | CancelCommandMessage
  | EmergencyStopMessage
  | ShutdownMessage
  | ProtocolErrorMessage;

export type GammaMessageHandler = (message: GammaControlMessage) => void | Promise<void>;

export type MinecraftControlClientStatus = Readonly<{
  state: MinecraftControlClientState;
  connectionId: string | null;
  lastReceivedSequence: number | null;
}>;

export type MinecraftControlDisconnect = Readonly<{
  kind: 'local' | 'clean' | 'abrupt' | 'protocol_error' | 'handshake_timeout';
  code: number | null;
}>;

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: Error) => void;
  settled: boolean;
};

export class MinecraftControlClientError extends Error {
  readonly category:
    | 'configuration'
    | 'invalid_state'
    | 'handshake_failed'
    | 'connection_lost'
    | 'protocol_violation'
    | 'delivery_failed';

  constructor(
    category: MinecraftControlClientError['category'],
    message: string
  ) {
    super(message);
    this.name = 'MinecraftControlClientError';
    this.category = category;
  }
}

/** Validate and return an exact literal-loopback Minecraft control URL. */
export function validateMinecraftControlUrl(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) {
    throw configurationError('Control URL must be an exact literal-loopback WebSocket URL');
  }
  const match = /^ws:\/\/(?:(\d{1,3}(?:\.\d{1,3}){3})|\[([^\]]+)\]):([0-9]+)(\/v1\/minecraft\/control)$/.exec(
    value
  );
  if (match === null) {
    throw configurationError('Control URL must be an exact literal-loopback WebSocket URL');
  }

  const ipv4 = match[1];
  const ipv6 = match[2];
  const portText = match[3];
  if (portText === undefined || !/^(?:0|[1-9][0-9]*)$/.test(portText)) {
    throw configurationError('Control URL must contain a valid explicit port');
  }
  const port = Number(portText);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw configurationError('Control URL must contain a valid explicit port');
  }

  if (ipv4 !== undefined) {
    const octets = ipv4.split('.');
    const canonical = octets.every((part) => {
      const octet = Number(part);
      return Number.isInteger(octet) && octet >= 0 && octet <= 255 && String(octet) === part;
    });
    if (!canonical || octets[0] !== '127') {
      throw configurationError('Control URL host must be a literal loopback address');
    }
  } else if (
    ipv6 === undefined ||
    isIP(ipv6) !== 6 ||
    !IPV6_LOOPBACKS.check(ipv6, 'ipv6')
  ) {
    throw configurationError('Control URL host must be a literal loopback address');
  }

  try {
    const parsed = new URL(value);
    if (
      parsed.protocol !== 'ws:' ||
      parsed.pathname !== CONTROL_PATH ||
      parsed.username !== '' ||
      parsed.password !== '' ||
      parsed.search !== '' ||
      parsed.hash !== ''
    ) {
      throw new Error('invalid');
    }
  } catch {
    throw configurationError('Control URL must be an exact literal-loopback WebSocket URL');
  }
  return value;
}

/** Validate a bearer value without returning, logging, or embedding it in an error. */
export function validateMinecraftControlToken(value: unknown): asserts value is string {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value !== value.trim() ||
    !/^[\x21-\x7e]+$/.test(value)
  ) {
    throw configurationError('Control token is not usable');
  }
}

export class MinecraftControlClient {
  readonly #url: string;
  readonly #controlToken: string;
  readonly #hello: MinecraftHelloIdentity;
  readonly #maximumInboundBytes: number;
  readonly #handshakeTimeoutMs: number;
  readonly #now: () => Date;
  readonly #createMessageId: () => string;

  #state: MinecraftControlClientState = 'disconnected';
  #socket: WebSocket | undefined;
  #generation = 0;
  #connectionId: string | null = null;
  #lastReceivedSequence: number | null = null;
  #outboundSequence = 0;
  #maximumOutboundBytes = DEFAULT_MAXIMUM_INBOUND_BYTES;
  #handler: GammaMessageHandler | undefined;
  #connectDeferred: Deferred<WelcomeMessage> | undefined;
  #disconnectDeferred: Deferred<MinecraftControlDisconnect> | undefined;
  #lastDisconnect: MinecraftControlDisconnect = Object.freeze({ kind: 'local', code: null });
  #handshakeTimer: NodeJS.Timeout | undefined;
  #forcedCloseTimer: NodeJS.Timeout | undefined;
  #sendTail: Promise<void> = Promise.resolve();
  #inboundTail: Promise<void> = Promise.resolve();
  #handlerTail: Promise<void> = Promise.resolve();
  #usedMessageIds = new Set<string>();
  #receivedMessageIds = new Set<string>();
  #localCloseGeneration: number | null = null;
  #forcedDisconnectKind:
    | 'protocol_error'
    | 'handshake_timeout'
    | undefined;

  constructor(options: MinecraftControlClientOptions) {
    if (options === null || typeof options !== 'object') {
      throw configurationError('Control client options are required');
    }
    this.#url = validateMinecraftControlUrl(options.url);
    validateMinecraftControlToken(options.controlToken);
    this.#controlToken = options.controlToken;
    this.#maximumInboundBytes = boundedInteger(
      options.maximumInboundBytes ?? DEFAULT_MAXIMUM_INBOUND_BYTES,
      1,
      MAXIMUM_INBOUND_BYTES_LIMIT,
      'Maximum inbound bytes'
    );
    this.#handshakeTimeoutMs = boundedInteger(
      options.handshakeTimeoutMs ?? DEFAULT_HANDSHAKE_TIMEOUT_MS,
      1,
      MAXIMUM_HANDSHAKE_TIMEOUT_MS,
      'Handshake timeout'
    );
    if (typeof options.now !== 'undefined' && typeof options.now !== 'function') {
      throw configurationError('Clock must be callable');
    }
    if (
      typeof options.createMessageId !== 'undefined' &&
      typeof options.createMessageId !== 'function'
    ) {
      throw configurationError('Message identifier factory must be callable');
    }
    this.#now = options.now ?? (() => new Date());
    this.#createMessageId = options.createMessageId ?? (() => crypto.randomUUID());
    this.#hello = validateHelloIdentity(options.hello);
    if (containsSecret(this.#hello, this.#controlToken)) {
      throw configurationError('Hello identity is invalid');
    }
  }

  connect(): Promise<WelcomeMessage> {
    if (this.#state === 'connecting' || this.#state === 'awaiting_welcome') {
      return this.#connectDeferred?.promise ?? Promise.reject(invalidStateError());
    }
    if (this.#state !== 'disconnected') {
      return Promise.reject(invalidStateError());
    }

    const generation = ++this.#generation;
    this.#state = 'connecting';
    this.#connectionId = null;
    this.#lastReceivedSequence = null;
    this.#outboundSequence = 0;
    this.#maximumOutboundBytes = DEFAULT_MAXIMUM_INBOUND_BYTES;
    this.#sendTail = Promise.resolve();
    this.#inboundTail = Promise.resolve();
    this.#handlerTail = Promise.resolve();
    this.#usedMessageIds = new Set<string>();
    this.#receivedMessageIds = new Set<string>();
    this.#localCloseGeneration = null;
    this.#forcedDisconnectKind = undefined;
    this.#connectDeferred = deferred<WelcomeMessage>();
    this.#disconnectDeferred = deferred<MinecraftControlDisconnect>();

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.#url, {
        headers: { Authorization: `Bearer ${this.#controlToken}` },
        maxPayload: this.#maximumInboundBytes,
        perMessageDeflate: false,
        followRedirects: false,
        skipUTF8Validation: false
      });
    } catch {
      this.#finishWithoutSocket(generation, 'connection_lost');
      return this.#connectDeferred.promise;
    }
    this.#socket = socket;

    this.#handshakeTimer = setTimeout(() => {
      if (!this.#isCurrent(generation, socket) || this.#state === 'ready') return;
      this.#forcedDisconnectKind = 'handshake_timeout';
      this.#rejectPendingConnect(
        new MinecraftControlClientError('handshake_failed', 'Control handshake timed out')
      );
      this.#closeSocket(generation, 1008, 'handshake timeout');
    }, this.#handshakeTimeoutMs);
    this.#handshakeTimer.unref();

    socket.once('open', () => {
      if (!this.#isCurrent(generation, socket) || this.#state !== 'connecting') return;
      this.#state = 'awaiting_welcome';
      let hello: string;
      try {
        hello = this.#buildHello();
      } catch {
        this.#rejectPendingConnect(
          new MinecraftControlClientError('handshake_failed', 'Could not create control hello')
        );
        this.#closeSocket(generation, 1008, 'invalid hello');
        return;
      }
      void this.#sendSerialized(hello, generation).catch(() => {
        if (!this.#isCurrent(generation, socket)) return;
        this.#rejectPendingConnect(connectionLostError());
        this.#closeSocket(generation, 1011, 'hello delivery failed');
      });
    });

    socket.on('message', (data, isBinary) => {
      if (!this.#isCurrent(generation, socket)) return;
      const operation = this.#inboundTail.then(() =>
        this.#handleFrame(data, isBinary, generation)
      );
      this.#inboundTail = operation.catch(() => undefined);
    });

    socket.once('error', () => {
      if (!this.#isCurrent(generation, socket)) return;
      this.#rejectPendingConnect(connectionLostError());
      this.#closeSocket(generation, 1011, 'control connection failed');
    });

    socket.once('close', (code) => {
      this.#finishSocket(generation, code);
    });

    return this.#connectDeferred.promise;
  }

  async close(): Promise<void> {
    if (this.#state === 'disconnected') return;
    const generation = this.#generation;
    const disconnect = this.#disconnectDeferred?.promise;
    this.#localCloseGeneration = generation;
    this.#rejectPendingConnect(
      new MinecraftControlClientError('handshake_failed', 'Control connection closed locally')
    );
    this.#closeSocket(generation, 1000, 'client closing');
    if (disconnect !== undefined) await disconnect;
  }

  sendHeartbeat(companionState: CompanionState = 'DISCONNECTED'): Promise<void> {
    return this.#sendConnected({
      type: 'heartbeat',
      payload: {
        companion_state: companionState,
        last_received_sequence: this.#lastReceivedSequence
      }
    });
  }

  sendSidecarMessage(message: SidecarOutboundMessage): Promise<void> {
    if (containsSecret(message, this.#controlToken)) {
      return Promise.reject(
        new MinecraftControlClientError('protocol_violation', 'Invalid sidecar protocol message')
      );
    }
    return this.#sendConnected(message);
  }

  setMessageHandler(handler: GammaMessageHandler | undefined): void {
    if (handler !== undefined && typeof handler !== 'function') {
      throw new TypeError('Gamma message handler must be callable');
    }
    this.#handler = handler;
  }

  status(): Readonly<MinecraftControlClientStatus> {
    return Object.freeze({
      state: this.#state,
      connectionId: this.#connectionId,
      lastReceivedSequence: this.#lastReceivedSequence
    });
  }

  waitForDisconnect(): Promise<Readonly<MinecraftControlDisconnect>> {
    return this.#disconnectDeferred?.promise ?? Promise.resolve(this.#lastDisconnect);
  }

  #buildHello(): string {
    const message = parseProtocolMessage({
      protocol: PROTOCOL_NAME,
      version: PROTOCOL_VERSION,
      type: 'hello',
      message_id: this.#safeMessageId(),
      sent_at: this.#safeTimestamp(),
      sequence: this.#outboundSequence,
      payload: {
        supported_versions: [PROTOCOL_VERSION],
        sidecar_instance_id: this.#hello.sidecarInstanceId,
        sidecar_build: this.#hello.sidecarBuild,
        capabilities: ['companion_v1'],
        node_version: this.#hello.nodeVersion,
        minecraft_library_version: this.#hello.minecraftLibraryVersion,
        pathfinder_version: this.#hello.pathfinderVersion,
        companion_state: 'DISCONNECTED'
      }
    });
    if (message.type !== 'hello') throw new Error('invalid hello');
    this.#outboundSequence += 1;
    return JSON.stringify(message);
  }

  #sendConnected(message: SidecarOutboundMessage | Readonly<{
    type: 'heartbeat';
    payload: { companion_state: CompanionState; last_received_sequence: number | null };
  }>): Promise<void> {
    if (this.#state !== 'ready' || this.#connectionId === null) {
      return Promise.reject(invalidStateError());
    }
    const generation = this.#generation;
    let serialized: string;
    try {
      const candidate = parseProtocolMessage({
        ...message,
        protocol: PROTOCOL_NAME,
        version: PROTOCOL_VERSION,
        message_id: this.#safeMessageId(),
        connection_id: this.#connectionId,
        sent_at: this.#safeTimestamp(),
        sequence: this.#outboundSequence
      });
      if (!isSidecarConnectedMessage(candidate)) throw new Error('invalid outbound direction');
      if (containsSecret(candidate, this.#controlToken)) throw new Error('secret in message');
      serialized = JSON.stringify(candidate);
      if (Buffer.byteLength(serialized, 'utf8') > this.#maximumOutboundBytes) {
        throw new Error('too large');
      }
      this.#outboundSequence += 1;
    } catch {
      return Promise.reject(
        new MinecraftControlClientError('protocol_violation', 'Invalid sidecar protocol message')
      );
    }

    const operation = this.#sendTail
      .then(() => this.#sendSerialized(serialized, generation))
      .catch(() => {
        if (this.#isCurrent(generation)) {
          this.#closeSocket(generation, 1011, 'control delivery failed');
        }
        throw new MinecraftControlClientError(
          'delivery_failed',
          'Control message delivery failed'
        );
      });
    this.#sendTail = operation.catch(() => undefined);
    return operation;
  }

  #sendSerialized(serialized: string, generation: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = this.#socket;
      if (!this.#isCurrent(generation) || socket?.readyState !== WebSocket.OPEN) {
        reject(connectionLostError());
        return;
      }
      socket.send(serialized, { binary: false, compress: false }, (error) => {
        if (!this.#isCurrent(generation) || (error !== undefined && error !== null)) {
          reject(
            new MinecraftControlClientError('delivery_failed', 'Control message delivery failed')
          );
          return;
        }
        resolve();
      });
    });
  }

  async #handleFrame(data: RawData, isBinary: boolean, generation: number): Promise<void> {
    if (!this.#isCurrent(generation) || this.#state === 'closing') return;
    try {
      if (isBinary) throw new Error('binary');
      const bytes = rawDataToBuffer(data);
      if (bytes.byteLength > this.#maximumInboundBytes) throw new Error('too large');
      const text = UTF8_DECODER.decode(bytes);
      const value = parseDuplicateSafeJsonObject(text);
      const message = parseProtocolMessage(value);
      if (!isGammaMessage(message)) throw new Error('wrong direction');
      if (containsSecret(message, this.#controlToken)) throw new Error('secret in message');
      if (this.#receivedMessageIds.has(message.message_id)) throw new Error('duplicate message id');
      this.#receivedMessageIds.add(message.message_id);

      if (this.#state === 'awaiting_welcome') {
        if (message.type === 'protocol_error') {
          this.#rejectPendingConnect(
            new MinecraftControlClientError('handshake_failed', 'Control handshake was rejected')
          );
          this.#forcedDisconnectKind = 'protocol_error';
          this.#closeSocket(generation, 1002, 'handshake rejected');
          return;
        }
        if (message.type !== 'welcome') throw new Error('welcome required');
        if (
          message.payload.selected_version !== PROTOCOL_VERSION ||
          message.sequence !== 0
        ) {
          throw new Error('invalid welcome');
        }
        this.#connectionId = message.connection_id;
        this.#lastReceivedSequence = message.sequence;
        this.#maximumOutboundBytes = message.payload.maximum_message_bytes;
        this.#state = 'ready';
        this.#clearHandshakeTimer();
        this.#resolvePendingConnect(message);
        return;
      }

      if (this.#state !== 'ready' || message.type === 'welcome') {
        throw new Error('message outside active session');
      }
      if (message.connection_id !== this.#connectionId) throw new Error('stale session');
      if (
        this.#lastReceivedSequence !== null &&
        message.sequence <= this.#lastReceivedSequence
      ) {
        throw new Error('non-monotonic sequence');
      }
      this.#lastReceivedSequence = message.sequence;

      const handler = this.#handler;
      if (message.type === 'emergency_stop') {
        if (handler !== undefined) {
          try {
            await handler(message);
          } catch {
            this.#handlerFailed(generation);
          }
        }
        return;
      }

      const completion = this.#handlerTail.then(async () => {
        if (this.#state === 'ready' && this.#isCurrent(generation) && handler !== undefined) {
          await handler(message);
        }
      });
      this.#handlerTail = completion.catch(() => this.#handlerFailed(generation));

      if (message.type === 'shutdown') {
        await this.#handlerTail;
        if (this.#isCurrent(generation)) {
          this.#localCloseGeneration = generation;
          this.#closeSocket(generation, 1000, 'shutdown received');
        }
      }
    } catch {
      this.#protocolViolation(generation);
    }
  }

  #handlerFailed(generation: number): void {
    if (!this.#isCurrent(generation) || this.#state === 'closing') return;
    this.#forcedDisconnectKind = 'protocol_error';
    this.#closeSocket(generation, 1011, 'message handler failed');
  }

  #protocolViolation(generation: number): void {
    if (!this.#isCurrent(generation)) return;
    this.#forcedDisconnectKind = 'protocol_error';
    this.#rejectPendingConnect(
      new MinecraftControlClientError('protocol_violation', 'Invalid control protocol message')
    );
    this.#closeSocket(generation, 1008, 'invalid protocol message');
  }

  #closeSocket(generation: number, code: number, reason: string): void {
    if (!this.#isCurrent(generation)) return;
    this.#clearHandshakeTimer();
    this.#state = 'closing';
    const socket = this.#socket;
    if (socket === undefined || socket.readyState === WebSocket.CLOSED) {
      this.#finishSocket(generation, code);
      return;
    }
    try {
      if (socket.readyState === WebSocket.CONNECTING) socket.terminate();
      else if (socket.readyState === WebSocket.OPEN) socket.close(code, reason);
    } catch {
      try {
        socket.terminate();
      } catch {
        this.#finishSocket(generation, code);
      }
    }
    this.#clearForcedCloseTimer();
    this.#forcedCloseTimer = setTimeout(() => {
      if (!this.#isCurrent(generation) || this.#state === 'disconnected') return;
      try {
        socket.terminate();
      } catch {
        this.#finishSocket(generation, code);
      }
    }, 500);
    this.#forcedCloseTimer.unref();
  }

  #finishSocket(generation: number, code: number): void {
    if (!this.#isCurrent(generation)) return;
    this.#clearHandshakeTimer();
    this.#clearForcedCloseTimer();
    const wasLocal = this.#localCloseGeneration === generation;
    const kind = this.#forcedDisconnectKind ?? (wasLocal ? 'local' : code === 1000 ? 'clean' : 'abrupt');
    const result: MinecraftControlDisconnect = Object.freeze({ kind, code });
    this.#lastDisconnect = result;
    this.#state = 'disconnected';
    this.#connectionId = null;
    this.#lastReceivedSequence = null;
    this.#socket = undefined;
    this.#rejectPendingConnect(connectionLostError());
    this.#resolveDisconnect(result);
  }

  #finishWithoutSocket(
    generation: number,
    category: MinecraftControlClientError['category']
  ): void {
    if (!this.#isCurrent(generation)) return;
    this.#state = 'disconnected';
    this.#socket = undefined;
    const result: MinecraftControlDisconnect = Object.freeze({ kind: 'abrupt', code: null });
    this.#lastDisconnect = result;
    this.#rejectPendingConnect(
      new MinecraftControlClientError(category, 'Control connection could not be established')
    );
    this.#resolveDisconnect(result);
  }

  #safeMessageId(): string {
    const value = this.#createMessageId();
    if (
      typeof value !== 'string' ||
      value.includes(this.#controlToken) ||
      this.#usedMessageIds.has(value)
    ) {
      throw new Error('invalid message id');
    }
    this.#usedMessageIds.add(value);
    return value;
  }

  #safeTimestamp(): string {
    const value = this.#now();
    if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
      throw new Error('invalid clock');
    }
    return value.toISOString();
  }

  #isCurrent(generation: number, socket?: WebSocket): boolean {
    return (
      generation === this.#generation &&
      this.#state !== 'disconnected' &&
      (socket === undefined || socket === this.#socket)
    );
  }

  #clearHandshakeTimer(): void {
    if (this.#handshakeTimer !== undefined) {
      clearTimeout(this.#handshakeTimer);
      this.#handshakeTimer = undefined;
    }
  }

  #clearForcedCloseTimer(): void {
    if (this.#forcedCloseTimer !== undefined) {
      clearTimeout(this.#forcedCloseTimer);
      this.#forcedCloseTimer = undefined;
    }
  }

  #rejectPendingConnect(error: Error): void {
    const pending = this.#connectDeferred;
    if (pending === undefined || pending.settled) return;
    pending.settled = true;
    pending.reject(error);
  }

  #resolvePendingConnect(welcome: WelcomeMessage): void {
    const pending = this.#connectDeferred;
    if (pending === undefined || pending.settled) return;
    pending.settled = true;
    pending.resolve(welcome);
  }

  #resolveDisconnect(result: MinecraftControlDisconnect): void {
    const pending = this.#disconnectDeferred;
    if (pending === undefined || pending.settled) return;
    pending.settled = true;
    pending.resolve(result);
  }
}

function validateHelloIdentity(value: unknown): MinecraftHelloIdentity {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw configurationError('Hello identity is invalid');
  }
  const identity = value as Partial<MinecraftHelloIdentity>;
  const candidate = {
    protocol: PROTOCOL_NAME,
    version: PROTOCOL_VERSION,
    type: 'hello',
    message_id: 'validation-message',
    sent_at: '2026-01-01T00:00:00Z',
    sequence: 0,
    payload: {
      supported_versions: [PROTOCOL_VERSION],
      sidecar_instance_id: identity.sidecarInstanceId,
      sidecar_build: identity.sidecarBuild,
      capabilities: ['companion_v1'],
      node_version: identity.nodeVersion,
      minecraft_library_version: identity.minecraftLibraryVersion,
      pathfinder_version: identity.pathfinderVersion,
      companion_state: 'DISCONNECTED'
    }
  };
  try {
    const parsed = parseProtocolMessage(candidate);
    if (parsed.type !== 'hello') throw new Error('wrong type');
    return Object.freeze({
      sidecarInstanceId: parsed.payload.sidecar_instance_id,
      sidecarBuild: parsed.payload.sidecar_build,
      nodeVersion: parsed.payload.node_version,
      minecraftLibraryVersion: parsed.payload.minecraft_library_version,
      pathfinderVersion: parsed.payload.pathfinder_version
    });
  } catch {
    throw configurationError('Hello identity is invalid');
  }
}

function parseDuplicateSafeJsonObject(text: string): Record<string, unknown> {
  const objectKeys: Set<string>[] = [];
  let invalid = false;
  try {
    visit(
      text,
      {
        onObjectBegin: () => {
          objectKeys.push(new Set<string>());
        },
        onObjectProperty: (property) => {
          const keys = objectKeys.at(-1);
          if (keys === undefined || keys.has(property)) invalid = true;
          else keys.add(property);
        },
        onObjectEnd: () => {
          objectKeys.pop();
        },
        onError: () => {
          invalid = true;
        }
      },
      { disallowComments: true, allowTrailingComma: false, allowEmptyContent: false }
    );
  } catch {
    invalid = true;
  }
  if (invalid || objectKeys.length !== 0) throw new Error('invalid JSON');

  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch {
    throw new Error('invalid JSON');
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('JSON object required');
  }
  return value as Record<string, unknown>;
}

function rawDataToBuffer(data: RawData): Buffer {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.concat(data);
  return Buffer.from(data);
}

function containsSecret(value: unknown, secret: string, seen = new WeakSet<object>()): boolean {
  if (typeof value === 'string') return value.includes(secret);
  if (value === null || typeof value !== 'object') return false;
  if (seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.some((item) => containsSecret(item, secret, seen));
  }
  return Object.values(value).some((item) => containsSecret(item, secret, seen));
}

function isGammaMessage(
  message: ReturnType<typeof parseProtocolMessage>
): message is WelcomeMessage | GammaControlMessage {
  return [
    'welcome',
    'command',
    'cancel_command',
    'emergency_stop',
    'shutdown',
    'protocol_error'
  ].includes(message.type);
}

function isSidecarConnectedMessage(
  message: ReturnType<typeof parseProtocolMessage>
): boolean {
  return [
    'heartbeat',
    'sidecar_status',
    'minecraft_status',
    'command_ack',
    'command_progress',
    'state_snapshot',
    'terminal_result'
  ].includes(message.type);
}

function boundedInteger(value: unknown, minimum: number, maximum: number, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw configurationError(`${label} must be an integer within its supported range`);
  }
  return value;
}

function deferred<T>(): Deferred<T> {
  let resolvePromise!: (value: T) => void;
  let rejectPromise!: (reason: Error) => void;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise, settled: false };
}

function configurationError(message: string): MinecraftControlClientError {
  return new MinecraftControlClientError('configuration', message);
}

function invalidStateError(): MinecraftControlClientError {
  return new MinecraftControlClientError(
    'invalid_state',
    'Control client is not ready for this operation'
  );
}

function connectionLostError(): MinecraftControlClientError {
  return new MinecraftControlClientError('connection_lost', 'Control connection was lost');
}
