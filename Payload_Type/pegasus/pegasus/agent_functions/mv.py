from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class MvArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="source",
                type=ParameterType.String,
                description="Source file or directory path",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
            CommandParameter(
                name="destination",
                type=ParameterType.String,
                description="Destination path",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line) > 0 and self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        elif len(self.command_line) > 0:
            parts = self.command_line.split(" ", 1)
            if len(parts) < 2:
                raise ValueError("Usage: mv <source> <destination>")
            self.add_arg("source", parts[0])
            self.add_arg("destination", parts[1])
        else:
            raise ValueError("Missing source and destination arguments")


class MvCommand(CommandBase):
    cmd = "mv"
    needs_admin = False
    help_cmd = "mv <source> <destination>"
    description = "Move or rename file"
    version = 1
    author = "@Lavender-exe"
    argument_class = MvArguments
    attackmapping = ["T1074"]

    async def create_go_tasking(self, taskData: MythicCommandBase.PTTaskMessageAllData) -> MythicCommandBase.PTTaskCreateTaskingMessageResponse:
        response = MythicCommandBase.PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )
        response.DisplayParams = f"{taskData.args.get_arg('source')} -> {taskData.args.get_arg('destination')}"
        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
