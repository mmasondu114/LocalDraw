"""Second trial using the Edit author's short instructions and sampler settings."""
import verify_doctor_penguin_sequence as doctor
from pathlib import Path

t = doctor.trial
t.CFG.update(cfg=4.0, sampler_name='euler_ancestral', scheduler='simple')
t.NEGATIVE = ''
actions = [
    'Make the character bend both elbows and cradle a single small penguin in both hands in front of his chest. The penguin has black and white feathers, an orange beak and a red scarf. Move them into a lounge with a teal bench and a window. Keep the character\'s hood, dark face mask, clothing design and colors the same. Show the whole body.',
    'Turn the character to face right in profile and make him take a walking step, still holding the penguin in both arms. Keep the same character, clothing, penguin, red scarf and room. Show the whole body.',
    'Make the character sit down on the teal bench. Put the penguin on his lap, supported by both hands. Keep the same character, clothing, penguin, red scarf and room. Show both boots on the floor.',
    'Make the character crouch beside the bench and gently lower the penguin onto the floor with both hands. The penguin\'s feet touch the floor. Keep the same character, clothing, penguin, red scarf and room.',
]
t.prompt = lambda panel: actions[int(panel['id'])-1]

if __name__ == '__main__':
    previous = doctor.SOURCE
    for panel in t.PANELS:
        previous = t.render(panel, 'doctor-direct-' + panel['id'], previous)
