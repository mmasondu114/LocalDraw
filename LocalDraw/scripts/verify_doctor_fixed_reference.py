"""Check whether retaining the canonical reference avoids propagated errors."""
import verify_doctor_direct_edit as direct
t = direct.t
t.NEGATIVE = 'extra penguin, duplicate penguin, bird head on human, beak on human, exposed eyes, visible face, face paint, extra limbs, malformed hands, text, watermark'
actions = [
    'Change the pose so the Doctor bends both elbows and cradles one small penguin against his chest in both gloved hands.',
    'Change the pose so the Doctor walks toward the right in side profile, taking a clear step while holding one small penguin in both arms.',
    'Change the pose so the Doctor sits on a teal bench with both knees bent and both boots on the floor. One small penguin sits upright on his lap, supported by both hands.',
    'Change the pose so the Doctor crouches low beside a teal bench and sets one small penguin on the floor with both hands, the penguin feet touching the floor.',
]
shared = (' The Doctor is the human character from Arknights. Keep the reference hood, opaque black faceplate completely covering his face, coat design, turquoise upper arm stripe and orange sleeve emblem. '
          'The small penguin has black and white feathers, an orange beak and a red scarf. '
          'Set the background in a lounge with a teal bench and a window. Show the whole body.')
t.prompt = lambda panel: actions[int(panel['id'])-1] + shared

if __name__ == '__main__':
    for panel in t.PANELS:
        t.render(panel, 'doctor-fixed-' + panel['id'], direct.doctor.SOURCE, weight=0.8)
