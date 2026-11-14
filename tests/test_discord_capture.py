"""Standalone contract for bounded complete owner utterance capture."""
import io,math,struct,sys,unittest,wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from gamma.coordination.discord_capture import OwnerPCMBuffer

def pcm(frames, left=1000, right=3000):return struct.pack('<hh',left,right)*frames
def samples(items):
 out=[]
 for item in items:
  assert item['user_id']=='1' and 44<=len(item['wav'])<=800_000
  with wave.open(io.BytesIO(item['wav']),'rb') as f:
   assert (f.getframerate(),f.getnchannels(),f.getsampwidth())==(16000,1,2)
   assert f.getnframes()<=12*16000
   out.extend(struct.unpack('<'+'h'*f.getnframes(),f.readframes(f.getnframes())))
 return out
class Contract(unittest.TestCase):
 def test_long_question_remains_one_bounded_utterance(self):
  b=OwnerPCMBuffer('1');items=[]
  for n in range(350):items.extend(b.push('1',pcm(960),now=n*.02))
  self.assertEqual(items,[])
  item=b.flush('1');self.assertEqual(len(item['wav']),224044)
  self.assertEqual(len(samples([item])),112000)
  self.assertTrue(all(abs(x-2000)<=2 for x in samples([item])[100:]))
 def test_packet_boundary_invariance(self):
  signal=b''.join(pcm(1,n%3000-1500,1500-n%3000) for n in range(3007))
  a=OwnerPCMBuffer('1');b=OwnerPCMBuffer('1')
  a.push('1',signal,now=0)
  for n in range(0,len(signal),28):b.push('1',signal[n:n+28],now=n/192000)
  self.assertEqual(samples([a.flush('1')]),samples([b.flush('1')]))
 def test_alias_rejection_and_voice_band_preservation(self):
  def rms(hz):
   b=OwnerPCMBuffer('1');vals=[]
   for n in range(9600):
    x=round(12000*math.sin(2*math.pi*hz*n/48000));vals.append(struct.pack('<hh',x,x))
   b.push('1',b''.join(vals),now=0);v=samples([b.flush('1')])[100:]
   return math.sqrt(sum(x*x for x in v)/len(v))
  self.assertGreater(rms(1000),7000)
  self.assertLess(rms(12000),500)
 def test_continuous_capture_is_split_bounded_without_loss(self):
  b=OwnerPCMBuffer('1');items=[]
  for n in range(651):items.extend(b.push('1',pcm(960),now=n*.02))
  tail=b.flush('1')
  if tail:items.append(tail)
  self.assertEqual(len(items),2)
  self.assertEqual(len(samples(items)),651*320)
 def test_clear_flush_and_expire_reset_converter_state(self):
  for finish in ('clear','flush','expire'):
   b=OwnerPCMBuffer('1');b.push('1',pcm(961,30000,30000),now=0)
   if finish=='clear':b.clear()
   elif finish=='flush':b.flush('1')
   else:self.assertEqual(len(b.expire(now=.61)),1)
   b.push('1',pcm(960,0,0),now=1)
   self.assertEqual(set(samples([b.flush('1')])),{0})
 def test_rejected_pcm_does_not_poison_pending_audio(self):
  b=OwnerPCMBuffer('1');b.push('1',pcm(960),now=0)
  for user,data in [('2',pcm(960)),('1',b'x'),('1',b''),('1','bad'),('1',pcm(96001))]:
   self.assertEqual(b.push(user,data,now=.1),[])
  self.assertEqual(len(samples([b.flush('1')])),320)
 def test_silence_and_owner_scope(self):
  b=OwnerPCMBuffer('1');b.push('1',pcm(960),now=0)
  self.assertEqual(b.expire(now=.59),[])
  self.assertIsNone(b.flush('2'))
  self.assertEqual(len(samples(b.expire(now=.61))),320)
  self.assertEqual(b.expire(now=1),[])
  for owner in ('', '0', 'bad', True):
   with self.assertRaises(ValueError):OwnerPCMBuffer(owner)
if __name__=='__main__':unittest.main()
