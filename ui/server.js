import { WebSocketServer } from 'ws';

const PORT = 3001;
const rooms = new Map();

const wss = new WebSocketServer({ port: PORT });

wss.on('connection', (ws) => {
  ws.isAlive = true;

  ws.on('message', (data) => {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }

    if (msg.type === 'join') {
      const code = String(msg.code).trim();
      if (!code) {
        ws.send(JSON.stringify({ type: 'error', message: 'Invalid code' }));
        return;
      }

      let room = rooms.get(code);

      if (!room) {
        // First player — create room, assign White
        room = { code, white: ws, black: null };
        rooms.set(code, room);
        ws.room = code;
        ws.color = 'white';
        ws.send(JSON.stringify({ type: 'assigned', color: 'white', code }));
        ws.send(JSON.stringify({ type: 'waiting' }));
        console.log(`Room ${code} created. White joined.`);
        return;
      }

      if (room.black) {
        ws.send(JSON.stringify({ type: 'error', message: 'Room is full' }));
        return;
      }

      // Second player — assign Black
      room.black = ws;
      ws.room = code;
      ws.color = 'black';
      ws.send(JSON.stringify({ type: 'assigned', color: 'black', code }));
      console.log(`Room ${code}: Black joined. Game starting.`);

      // Notify both players the game is starting
      room.white.send(JSON.stringify({ type: 'start' }));
      room.black.send(JSON.stringify({ type: 'start' }));
      return;
    }

    // Relay game actions to the other player
    if (['move', 'undo', 'resign', 'reset'].includes(msg.type)) {
      const room = rooms.get(ws.room);
      if (!room) return;
      const other = ws.color === 'white' ? room.black : room.white;
      if (other && other.readyState === 1) {
        other.send(JSON.stringify(msg));
      }
    }
  });

  ws.on('close', () => {
    if (!ws.room) return;
    const room = rooms.get(ws.room);
    if (!room) return;

    const other = ws.color === 'white' ? room.black : room.white;
    if (other && other.readyState === 1) {
      other.send(JSON.stringify({ type: 'opponent-disconnected' }));
    }
    rooms.delete(ws.room);
    console.log(`Room ${ws.room} closed.`);
  });
});

console.log(`WebSocket server running on ws://0.0.0.0:${PORT}`);
