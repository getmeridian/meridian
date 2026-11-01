import type { Db } from 'sdk/db';

export interface Feedback {
  id: string;
  chat_id: number;
  message_id: number;
  sender: string;
  language: string;
  body: string | null;
  created_at: number;
  status: string;
  header_id: number;
  copy_id: number;
  acknowledged: number;
  lease: string;
  lease_until: number;
}

export class Store {
  constructor(private readonly db: Pick<Db, 'run' | 'get' | 'all'>) {}

  async purge(cutoff: number) {
    // The platform intentionally has no foreign keys. Delete dependent rows first.
    await this.db.run('DELETE FROM replies WHERE created_at < :cutoff OR NOT EXISTS (SELECT 1 FROM feedback WHERE id = replies.feedback_id AND created_at >= :cutoff)', { ':cutoff': cutoff });
    await this.db.run('DELETE FROM feedback WHERE created_at < :cutoff', { ':cutoff': cutoff });
  }

  async forget(chatId: number) {
    await this.db.run('DELETE FROM replies WHERE feedback_id IN (SELECT id FROM feedback WHERE chat_id = :chat)', { ':chat': chatId });
    await this.db.run('DELETE FROM feedback WHERE chat_id = :chat', { ':chat': chatId });
  }

  get(id: string) {
    return this.db.get<Feedback>('SELECT * FROM feedback WHERE id = :id', { ':id': id });
  }

  async receive(input: Pick<Feedback, 'id' | 'chat_id' | 'message_id' | 'sender' | 'language' | 'body' | 'created_at'>) {
    // Rate limit and insert are one SQL statement, so concurrent submissions cannot bypass it.
    await this.db.run(`INSERT INTO feedback (id, chat_id, message_id, sender, language, body, created_at)
      SELECT :id, :chat, :message, :sender, :lang, :body, :now
      WHERE (SELECT COUNT(*) FROM feedback WHERE chat_id = :chat AND created_at > :recent) < 5
      ON CONFLICT(id) DO NOTHING`, {
      ':id': input.id, ':chat': input.chat_id, ':message': input.message_id,
      ':sender': input.sender, ':lang': input.language, ':body': input.body,
      ':now': input.created_at, ':recent': input.created_at - 60,
    });
    return this.get(input.id);
  }

  forInbox(messageId: number) {
    return this.db.get<Feedback>('SELECT * FROM feedback WHERE header_id = :message OR copy_id = :message', { ':message': messageId });
  }

  async claim(id: string, lease: string, now: number) {
    const result = await this.db.run(`UPDATE feedback SET lease = :lease, lease_until = :until
      WHERE id = :id AND (lease = '' OR lease_until <= :now)`, {
      ':id': id, ':lease': lease, ':until': now + 300, ':now': now,
    });
    return result.rowsAffected === 1;
  }

  async updateDelivery(id: string, lease: string, field: 'header_id' | 'copy_id' | 'acknowledged', value: number) {
    // field is a closed union owned by this module's callers, never user SQL.
    const result = await this.db.run(`UPDATE feedback SET ${field} = :value WHERE id = :id AND lease = :lease`, {
      ':id': id, ':lease': lease, ':value': value,
    });
    if (result.rowsAffected !== 1) throw new Error('Feedback delivery lease was lost');
  }

  async release(id: string, lease: string) {
    await this.db.run("UPDATE feedback SET lease = '', lease_until = 0 WHERE id = :id AND lease = :lease", { ':id': id, ':lease': lease });
  }

  async done(id: string) {
    await this.db.run("UPDATE feedback SET status = 'done' WHERE id = :id", { ':id': id });
  }

  pending() {
    return this.db.all<Feedback>("SELECT * FROM feedback WHERE status = 'open' ORDER BY created_at LIMIT 20");
  }

  async claimReply(id: string, feedbackId: string, lease: string, now: number) {
    await this.db.run('INSERT INTO replies (id, feedback_id, created_at) SELECT :id, :feedback, :now WHERE EXISTS (SELECT 1 FROM feedback WHERE id = :feedback) ON CONFLICT(id) DO NOTHING', {
      ':id': id, ':feedback': feedbackId, ':now': now,
    });
    const result = await this.db.run(`UPDATE replies SET lease = :lease, lease_until = :until
      WHERE id = :id AND copy_id = 0 AND (lease = '' OR lease_until <= :now)`, {
      ':id': id, ':lease': lease, ':until': now + 300, ':now': now,
    });
    return result.rowsAffected === 1;
  }

  async finishReply(id: string, lease: string, copyId: number) {
    await this.db.run("UPDATE replies SET copy_id = :copy, lease = '', lease_until = 0 WHERE id = :id AND lease = :lease", {
      ':id': id, ':lease': lease, ':copy': copyId,
    });
  }

  async releaseReply(id: string, lease: string) {
    await this.db.run("UPDATE replies SET lease = '', lease_until = 0 WHERE id = :id AND lease = :lease", { ':id': id, ':lease': lease });
  }
}
