"""Guided recording of reference clips for cloned voices (roadmap 8.2).

``registers`` holds what the speaker reads, ``ffmpeg`` captures and cleans the microphone
input, ``session`` runs the interactive record / listen / redo loop. Uploading the clip is
``core.voices.VoiceLibrary``'s job, behind the CLI's consent prompt.
"""
