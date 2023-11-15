from aws_cdk import (
    Duration,
    Stack,
    aws_lex as lex,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as lambda_,
)

from constructs import Construct


class LexGenAIBot(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Iam role for bot to invoke lambda
        lex_cfn_role = iam.Role(
            self,
            "CfnLexGenAIDemoRole",
            assumed_by=iam.ServicePrincipal("lexv2.amazonaws.com"),
        )
        lex_cfn_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSLambdaExecute")
        )

        # S3 bucket for downloading the generated image
        self.input_bucket = s3.Bucket(
            self,
            "image-gen-output-bucket",
            versioned=True,
            bucket_name="image-gen-output-bucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        # lambda layer containing boto3, Pillow for image processing, and Pyshortener for shortening the pre-signed
        # s3 url.
        layer = lambda_.LayerVersion(
            self,
            "Boto3Layer",
            code=lambda_.Code.from_asset("./Boto3PillowPyshorteners.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
        )

        # lambda function for processing the incoming request from Lex Chatbot
        lambda_function = lambda_.Function(
            self,
            "LexGenAIBotLambda",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="LexBedrockBot.lambda_handler",
            code=lambda_.Code.from_asset("LexBedrockBot.zip"),
            layers=[layer],
            timeout=Duration.minutes(10),
            memory_size=2048,
            environment={"bucket": "image-gen-output-bucket"},
        )

        # lambda policy
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                    "lex:*",
                    "bedrock:*",
                    "logs:*",
                ],
                resources=["*"],
            )
        )
        lambda_function.grant_invoke(iam.ServicePrincipal("lexv2.amazonaws.com"))

        ### BOT SETUP

        # alias settings, where we define the lambda function with the ECR container with our LLM dialog code (defined in the lex-gen-ai-demo-docker-image directory)
        # test bot alias for demo, create a dedicated alias for serving traffic
        bot_alias_settings = lex.CfnBot.TestBotAliasSettingsProperty(
            bot_alias_locale_settings=[
                lex.CfnBot.BotAliasLocaleSettingsItemProperty(
                    bot_alias_locale_setting=lex.CfnBot.BotAliasLocaleSettingsProperty(
                        enabled=True,
                        code_hook_specification=lex.CfnBot.CodeHookSpecificationProperty(
                            lambda_code_hook=lex.CfnBot.LambdaCodeHookProperty(
                                code_hook_interface_version="1.0",
                                lambda_arn=lambda_function.function_arn,
                            )
                        ),
                    ),
                    locale_id="en_US",
                )
            ]
        )

        # lambda itself is tied to alias but codehook settings are intent specific
        initial_response_codehook_settings = lex.CfnBot.InitialResponseSettingProperty(
            code_hook=lex.CfnBot.DialogCodeHookInvocationSettingProperty(
                enable_code_hook_invocation=True,
                is_active=True,
                post_code_hook_specification=lex.CfnBot.PostDialogCodeHookInvocationSpecificationProperty(),
            )
        )

        # attaching a closing setting for Welcome intent when the intent is fulfilled.
        intent_closing_setting_property = lex.CfnBot.IntentClosingSettingProperty(
            closing_response=lex.CfnBot.ResponseSpecificationProperty(
                message_groups_list=[
                    lex.CfnBot.MessageGroupProperty(
                        message=lex.CfnBot.MessageProperty(
                            plain_text_message=lex.CfnBot.PlainTextMessageProperty(
                                value="Hi there, I'm GenAI Bot. How can I help you?"
                            )
                        )
                    )
                ]
            )
        )

        # Welcome Intent
        welcome_intent = lex.CfnBot.IntentProperty(
            name="WelcomeIntent",
            initial_response_setting=initial_response_codehook_settings,
            sample_utterances=[
                lex.CfnBot.SampleUtteranceProperty(utterance="Hi"),
                lex.CfnBot.SampleUtteranceProperty(utterance="Hey there"),
                lex.CfnBot.SampleUtteranceProperty(utterance="I need some help"),
                lex.CfnBot.SampleUtteranceProperty(utterance="Help needed"),
                lex.CfnBot.SampleUtteranceProperty(utterance="Can I get some help?"),
            ],
            intent_closing_setting=intent_closing_setting_property,
        )

        # Image generation Intent
        image_gen_intent = lex.CfnBot.IntentProperty(
            name="GenerateImageIntent",
            initial_response_setting=initial_response_codehook_settings,
            fulfillment_code_hook=lex.CfnBot.FulfillmentCodeHookSettingProperty(
                enabled=True,
                is_active=True,
                post_fulfillment_status_specification=lex.CfnBot.PostFulfillmentStatusSpecificationProperty(),
            ),
            sample_utterances=[
                lex.CfnBot.SampleUtteranceProperty(utterance="Generate an image"),
                lex.CfnBot.SampleUtteranceProperty(utterance="Generate an image of"),
                lex.CfnBot.SampleUtteranceProperty(
                    utterance="Image to be generated for"
                ),
            ],
        )

        # Fallback Intent
        fallback_intent = lex.CfnBot.IntentProperty(
            name="FallbackIntent",
            parent_intent_signature="AMAZON.FallbackIntent",
            initial_response_setting=initial_response_codehook_settings,
            fulfillment_code_hook=lex.CfnBot.FulfillmentCodeHookSettingProperty(
                enabled=True,
                is_active=True,
                post_fulfillment_status_specification=lex.CfnBot.PostFulfillmentStatusSpecificationProperty(),
            ),
        )

        # Create actual Lex Bot
        cfn_bot = lex.CfnBot(
            self,
            "LexGenAIBot",
            data_privacy={"ChildDirected": "false"},
            idle_session_ttl_in_seconds=300,
            name="LexGenAIBot",
            description="Bot created demonstration of GenAI capabilities.",
            role_arn=lex_cfn_role.role_arn,
            bot_locales=[
                lex.CfnBot.BotLocaleProperty(
                    locale_id="en_US",
                    nlu_confidence_threshold=0.4,
                    intents=[welcome_intent, image_gen_intent, fallback_intent],
                )
            ],
            test_bot_alias_settings=bot_alias_settings,
            auto_build_bot_locales=True,
        )
