import aws_cdk as cdk

from lex_genai_bot_cdk_files.lex_genai_bot_cdk_stack import LexGenAIBot

app = cdk.App()
filestack = LexGenAIBot(app, "LexGenAIBotStack")

app.synth()
