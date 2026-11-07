from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class SleepArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="interval",
                type=ParameterType.Number,
                description="Callback interval in seconds",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
            CommandParameter(
                name="jitter",
                type=ParameterType.Number,
                description="Callback jitter percentage (0-100)",
                default_value=10,
                parameter_group_info=[ParameterGroupInfo(required=False)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0 and self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        elif len(self.command_line) > 0:
            parts = self.command_line.split()
            self.add_arg("interval", int(parts[0]))
            if len(parts) > 1:
                self.add_arg("jitter", int(parts[1]))
        else:
            raise ValueError("Missing interval argument")


class SleepCommand(CommandBase):
    cmd = "sleep"
    needs_admin = False
    help_cmd = "sleep <interval> [jitter]"
    description = "Change callback interval and jitter"
    version = 1
    author = "@Lavender-exe"
    argument_class = SleepArguments
    attackmapping = ["T1029"]

    async def create_go_tasking(self, taskData: MythicCommandBase.PTTaskMessageAllData) -> MythicCommandBase.PTTaskCreateTaskingMessageResponse:
        response = MythicCommandBase.PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )
        response.DisplayParams = f"interval={taskData.args.get_arg('interval')}s jitter={taskData.args.get_arg('jitter')}%"
        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
